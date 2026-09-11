"""Speech-to-text backend selection.

The work Spark endpoint is OpenAI-compatible. A fast probe decides whether to
send the audio there, as 15-minute 32 kbps mp3 chunks (the endpoint caps upload
size). Any probe or transcription failure falls back to the local Canary
model; an unavailable work endpoint never blocks transcription.
"""
import array
import contextlib
import dataclasses
from dataclasses import dataclass
import difflib
import logging
import math
from pathlib import Path
import subprocess
import tempfile
import wave

import httpx

from .config import LOCAL_ASR_DEFAULT_LANGUAGE
from .connections import get_connection
from .models import get_local_asr

log = logging.getLogger(__name__)

# Why the remote engine was skipped on the last run, kept for the settings panel.
_last_remote_error = None

# The two names the whole product uses for where processing happens. "Spark" is
# internal hardware jargon and never reaches a user-facing string.
REMOTE_LABEL = "Remote engine"
LOCAL_LABEL = "On this computer"


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None
    # Which capture track this text came from on a two-track recording:
    # "mic" (the room) or "system" (loopback). None on mono recordings.
    channel: str | None = None


@dataclass
class Transcript:
    segments: list[Segment]
    duration: float
    backend: str


def remote_status():
    """Return public backend state without leaking internal endpoint details."""
    endpoint = get_connection("transcription")["endpoint"]
    if not endpoint:
        return {"available": False, "backend": "local", "label": LOCAL_LABEL}
    try:
        base = endpoint.removesuffix("/v1/audio/transcriptions").rstrip("/")
        response = httpx.get(f"{base}/v1/models", timeout=2.0)
        if response.is_success:
            return {"available": True, "backend": "spark", "label": REMOTE_LABEL}
    except (httpx.HTTPError, OSError):
        pass
    return {"available": False, "backend": "local", "label": LOCAL_LABEL}


# Preserve the original recording, but give speech models a cleaner, stable
# mono signal: remove rumble/hiss, then gently level spoken passages.
# speechnorm (not dynaudnorm) on purpose: dynaudnorm lifts the gain of silent
# stretches up to speech level, which makes the model hallucinate short filler
# phrases into pauses. The lowpass sits just under Nyquist so Czech sibilants
# (s/s/c/r) survive.
MODEL_AUDIO_FILTER = "highpass=f=70,lowpass=f=7900,afftdn=nr=8:nf=-35,speechnorm=e=6.25:r=0.0005:l=1"


@contextlib.contextmanager
def _model_audio(wav_path):
    """Yield an enhanced temporary WAV, falling back to the original on failure."""
    with tempfile.TemporaryDirectory() as tmp:
        enhanced = Path(tmp) / "enhanced.wav"
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(wav_path), "-vn",
                    "-af", MODEL_AUDIO_FILTER, "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(enhanced),
                ],
                capture_output=True, text=True,
            )
        except OSError as exc:
            log.warning("Audio enhancement unavailable; using original recording: %s", exc)
            yield wav_path
            return
        if proc.returncode != 0 or not enhanced.is_file():
            log.warning(
                "Audio enhancement failed; using original recording: %s",
                (proc.stderr or "").strip()[-500:],
            )
            yield wav_path
            return
        yield enhanced


# 15 min of 32 kbps mono mp3 is ~3.5 MB, comfortably under the remote's
# per-file size limit whatever a meeting's real length turns out to be.
CHUNK_SECONDS = 900
def _mp3_chunks(wav_path, out_dir):
    """Split+compress the wav into upload-sized mp3 slices, in order."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(wav_path), "-vn", "-ac", "1",
            "-c:a", "libmp3lame", "-b:a", "32k",
            "-f", "segment", "-segment_time", str(CHUNK_SECONDS), "-reset_timestamps", "1",
            str(Path(out_dir) / "part%04d.mp3"),
        ],
        check=True, capture_output=True, text=True,
    )
    return sorted(Path(out_dir).glob("part*.mp3"))


# Whisper-style decoders fill silence with short stock phrases ("Thank you.",
# "Deku ji."). Every such segment the endpoint returns is flagged by a high
# no-speech probability together with a poor average token logprob; real
# speech fails at least one of the two conditions.
NO_SPEECH_PROB_MAX = 0.6
AVG_LOGPROB_MIN = -1.0


def _is_hallucination(raw):
    """True for a verbose_json segment that is silence dressed up as speech."""
    if not str(raw.get("text", "")).strip():
        return True
    no_speech, avg_logprob = raw.get("no_speech_prob"), raw.get("avg_logprob")
    if no_speech is None or avg_logprob is None:
        return False
    return float(no_speech) > NO_SPEECH_PROB_MAX and float(avg_logprob) < AVG_LOGPROB_MIN


def _post_audio(path, language, timeout=3600.0, mime="audio/mpeg"):
    """One chunk -> (segments, duration), both relative to the chunk's own start.

    ``mime`` matches whatever bytes ``path`` actually holds: mp3 for batch
    chunks, wav for a live-draft window posted straight through with no
    ffmpeg transcode.
    """
    connection = get_connection("transcription")
    data = {"model": connection["model"], "response_format": "verbose_json"}
    if language:
        data["language"] = language
    with path.open("rb") as audio:
        response = httpx.post(
            connection["endpoint"],
            data=data,
            files={"file": (path.name, audio, mime)},
            timeout=timeout,
        )
    response.raise_for_status()
    body = response.json()
    # The endpoint reports some failures (missing audio support, bad model) as
    # an error body under HTTP 200; without this an empty transcript would win.
    if body.get("error"):
        raise ValueError(str(body["error"]))
    raw_segments = body.get("segments") or []
    if raw_segments:
        segments = [
            Segment(float(s.get("start", 0)), float(s.get("end", 0)), s.get("text", ""))
            for s in raw_segments
            if not _is_hallucination(s)
        ]
    else:
        segments = [Segment(0, float(body.get("duration", 0)), body.get("text", ""))]
    duration = float(body.get("duration") or max((s.end for s in segments), default=0))
    return segments, duration


def _remote_transcribe(wav_path, language, on_segment, timeout=3600.0):
    """Chunked remote transcription; segment times are stitched back together.

    Chunk offsets come from the segmenter's own grid, not from the durations
    the endpoint reports: mp3 encoder padding plus rounded ``duration`` values
    accumulate into a drift of seconds over a long meeting, which then shifts
    every speaker label assigned against the (undrifted) diarization timeline.
    """
    segments = []
    with tempfile.TemporaryDirectory() as tmp:
        chunks = _mp3_chunks(wav_path, tmp)
        total = len(chunks) * CHUNK_SECONDS  # only drives the progress bar
        duration = 0.0
        for index, chunk in enumerate(chunks):
            offset = index * CHUNK_SECONDS
            part, chunk_duration = _post_audio(chunk, language, timeout=timeout)
            for seg in part:
                shifted = dataclasses.replace(seg, start=seg.start + offset, end=seg.end + offset)
                segments.append(shifted)
                on_segment(shifted, total)
            duration = offset + (chunk_duration or CHUNK_SECONDS)
    return Transcript(segments, duration, "spark")


def _wav_duration(wav_path):
    """Audio length straight from the PCM header; no decode, no ffprobe."""
    with wave.open(str(wav_path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def _local_transcribe(wav_path, language, on_segment):
    """Canary over VAD-cut speech windows; silence never reaches the model.

    The language is always passed. Left to auto-detection, per-window language
    identification makes Czech recordings drift into Slovak and English.
    """
    duration = _wav_duration(wav_path)
    segments = []
    recognized = get_local_asr().recognize(
        str(wav_path),
        language=language or LOCAL_ASR_DEFAULT_LANGUAGE,
        pnc=True,
    )
    for raw in recognized:
        if not raw.text.strip():
            continue
        seg = Segment(raw.start, raw.end, raw.text)
        segments.append(seg)
        on_segment(seg, duration)
    return Transcript(segments, duration, "local")


# The remote Canary build answers with verbose_json but without per-segment
# confidences, so silence-hallucinations ("Dekuji." into a pause) survive the
# logprob filter. Their giveaway is loudness: they sit far below the level of
# anything actually spoken into the microphone. The thresholds are deliberately
# timid - measured on real recordings, genuine short interjections land within
# ~5 dB of the speech level, so 9 dB leaves a wide margin.
QUIET_SEGMENT_DB = 9.0
QUIET_SEGMENT_WORDS = 3


def _segment_dbfs(pcm, sample_rate, segment):
    """RMS level of one segment, in dBFS, from already-decoded mono samples."""
    lo = max(0, int(segment.start * sample_rate))
    hi = min(len(pcm), int(segment.end * sample_rate))
    if hi <= lo:
        return None
    mean_square = sum(float(v) * v for v in pcm[lo:hi]) / (hi - lo)
    return 20 * math.log10(math.sqrt(mean_square) / 32768 + 1e-9)


def drop_quiet_hallucinations(wav_path, segments):
    """Remove short segments transcribed out of near-silence.

    The reference is the recording's own 75th-percentile segment level, so a
    quiet speaker or a hot mic shifts the whole scale instead of breaking the
    rule. Anything the audio cannot be read for is kept.
    """
    if len(segments) < 4:
        return segments
    try:
        with wave.open(str(wav_path), "rb") as handle:
            if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
                return segments
            sample_rate = handle.getframerate()
            pcm = array.array("h")
            pcm.frombytes(handle.readframes(handle.getnframes()))
    except (wave.Error, OSError) as exc:
        log.warning("Level check skipped, keeping every segment: %s", exc)
        return segments
    levels = [_segment_dbfs(pcm, sample_rate, seg) for seg in segments]
    measured = sorted(level for level in levels if level is not None)
    if not measured:
        return segments
    speech_level = measured[int(len(measured) * 0.75)]
    kept = []
    for seg, level in zip(segments, levels):
        quiet = level is not None and level < speech_level - QUIET_SEGMENT_DB
        if quiet and len(seg.text.split()) <= QUIET_SEGMENT_WORDS:
            log.info("Dropping %.1f dBFS filler at %.1fs: %r", level, seg.start, seg.text.strip())
            continue
        kept.append(seg)
    return kept


# Track order written by the recorder's amerge; index is the wav channel.
CHANNEL_LABELS = ("mic", "system")
# A track whose loudest sample never gets near speech held nothing anybody
# said: transcribing it only buys hallucinations and doubles the bill.
CHANNEL_SILENT_DBFS = -45.0


def wav_channels(wav_path):
    """Channel count of a wav, or 1 for anything that cannot be read as one."""
    try:
        with wave.open(str(wav_path), "rb") as handle:
            return handle.getnchannels()
    except (wave.Error, EOFError, OSError):
        return 1


@contextlib.contextmanager
def channel_wav(wav_path, index):
    """Yield one channel of a multi-track recording as its own mono wav."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"channel{index}.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(wav_path), "-vn",
                "-filter_complex", f"pan=mono|c0=c{index}",
                "-c:a", "pcm_s16le", str(out),
            ],
            check=True, capture_output=True, text=True,
        )
        yield out


def _peak_dbfs(wav_path):
    """Peak level of a mono s16 wav, in dBFS.

    Peak, not average: a one-hour meeting with two spoken minutes averages
    like silence, and averaging that track away would throw the speech out
    with it.
    """
    with wave.open(str(wav_path), "rb") as handle:
        pcm = array.array("h")
        pcm.frombytes(handle.readframes(handle.getnframes()))
    if not pcm:
        return -99.0
    return 20 * math.log10(max(abs(min(pcm)), abs(max(pcm))) / 32768 + 1e-9)


def _transcribe_mono(wav_path, language, on_segment, timeout):
    """Full single-track pass: enhance, remote-or-local, drop silence fillers."""
    with _model_audio(wav_path) as model_audio:
        transcript = None
        if remote_status()["available"]:
            try:
                transcript = _remote_transcribe(model_audio, language, on_segment, timeout=timeout)
            except (httpx.HTTPError, ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
                global _last_remote_error
                detail = getattr(getattr(exc, "response", None), "text", "")
                _last_remote_error = f"{exc} {detail}".strip()[:1000]
                log.warning("Spark transcription failed, falling back to local: %s", _last_remote_error)
        if transcript is None:
            transcript = _local_transcribe(model_audio, language, on_segment)
        return dataclasses.replace(
            transcript, segments=drop_quiet_hallucinations(model_audio, transcript.segments)
        )


# Speakers in the room reach the microphone a little after the loopback has
# them digitally; allow for that plus segmenter jitter when pairing the two.
BLEED_TIME_SLACK = 1.5
# Two transcripts of the same words differ slightly (reverb, level); below this
# similarity they are two people saying related things, not one sound twice.
BLEED_TEXT_RATIO = 0.75


def _normalized_words(text):
    return "".join(ch for ch in text.lower() if ch.isalnum() or ch.isspace()).split()


def drop_speaker_bleed(mic_segments, system_segments):
    """Drop mic segments that are just the loudspeaker copy of the system track.

    Without headphones the microphone re-records whatever the machine plays, so
    both tracks transcribe the same sentence and the result reads as two people
    saying it in turn. The loopback copy is the clean one, so the microphone
    duplicate is what goes.
    """
    system_texts = [
        (seg.start, seg.end, " ".join(_normalized_words(seg.text))) for seg in system_segments
    ]
    kept = []
    for seg in mic_segments:
        text = " ".join(_normalized_words(seg.text))
        if not text:
            kept.append(seg)
            continue
        bleed = any(
            start - BLEED_TIME_SLACK <= seg.end and seg.start - BLEED_TIME_SLACK <= end
            and difflib.SequenceMatcher(None, text, other).ratio() >= BLEED_TEXT_RATIO
            for start, end, other in system_texts
            if other
        )
        if bleed:
            log.info("Dropping loudspeaker bleed at %.1fs: %r", seg.start, seg.text.strip())
            continue
        kept.append(seg)
    return kept


def transcribe(wav_path, language, on_segment=lambda segment, duration: None, timeout=3600.0):
    """Transcribe a recording, one pass per capture track.

    The recorder keeps the microphone and the system loopback on separate
    channels. Summing them first drowned the clean loopback in room reverb, so
    each track is transcribed on its own and the results are merged in time;
    the channel travels with every segment for speaker labelling downstream.
    Progress is reported across all tracks, so the bar never restarts, and the
    microphone's copy of what the speakers played is discarded afterwards.
    """
    if wav_channels(wav_path) < 2:
        return _transcribe_mono(wav_path, language, on_segment, timeout)
    with contextlib.ExitStack() as stack:
        tracks = []
        for index, label in enumerate(CHANNEL_LABELS):
            track = stack.enter_context(channel_wav(wav_path, index))
            level = _peak_dbfs(track)
            if level < CHANNEL_SILENT_DBFS:
                log.info("Skipping silent %s track (%.1f dBFS)", label, level)
                continue
            tracks.append((label, track))
        by_track, duration, backends = {}, 0.0, []
        for position, (label, track) in enumerate(tracks):
            # One pass is 1/len(tracks) of the work: shift each track's own
            # timeline into the combined one so the caller's percentage rises
            # monotonically instead of restarting per track.
            def on_track_segment(seg, track_duration, position=position):
                span = max(track_duration, 0.01)
                shifted = dataclasses.replace(seg, end=position * span + seg.end)
                on_segment(shifted, span * len(tracks))

            part = _transcribe_mono(track, language, on_track_segment, timeout)
            by_track[label] = [dataclasses.replace(seg, channel=label) for seg in part.segments]
            duration = max(duration, part.duration)
            backends.append(part.backend)
    if "mic" in by_track and "system" in by_track:
        by_track["mic"] = drop_speaker_bleed(by_track["mic"], by_track["system"])
    merged = sorted(
        (seg for segments in by_track.values() for seg in segments), key=lambda seg: seg.start
    )
    # "spark" only when every track really went there; a single local fallback
    # makes the whole transcript local for provenance purposes.
    backend = backends[0] if len(set(backends)) == 1 else "local"
    return Transcript(merged, duration, backend or "local")


def transcribe_live(wav_path, language, timeout, remote_available):
    """Transcribe an enhanced short live WAV using the session's backend probe."""
    with _model_audio(wav_path) as model_audio:
        if remote_available:
            try:
                segments, duration = _post_audio(model_audio, language, timeout=timeout, mime="audio/wav")
                return Transcript(segments, duration, "spark")
            except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
                global _last_remote_error
                detail = getattr(getattr(exc, "response", None), "text", "")
                _last_remote_error = f"{exc} {detail}".strip()[:1000]
                log.warning("Live draft window remote transcription failed, falling back to local: %s", _last_remote_error)
        return _local_transcribe(model_audio, language, lambda segment, duration: None)


def diagnose():
    """Why the remote engine is (not) working: probe, one real upload, last error."""
    connection = get_connection("transcription")
    report = {
        "url": connection["endpoint"],
        "model": connection["model"],
        "probe": remote_status(),
        "last_error": _last_remote_error,
    }
    if not connection["endpoint"]:
        report["upload"] = {
            "ok": False,
            "off": True,
            "detail": "No remote endpoint is configured; transcription runs on the local model.",
        }
        return report
    try:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.mp3"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                 "-ac", "1", "-c:a", "libmp3lame", "-b:a", "32k", str(probe)],
                check=True, capture_output=True,
            )
            segments, _ = _post_audio(probe, "cs")
        report["upload"] = {"ok": True, "detail": f"1s test audio accepted ({len(segments)} segments)"}
    except Exception as exc:  # any failure is the answer the user came for
        detail = getattr(getattr(exc, "response", None), "text", "") or str(exc)
        report["upload"] = {"ok": False, "detail": str(detail)[:1000]}
    return report

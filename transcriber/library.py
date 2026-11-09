"""The /data library: browsing, storing recordings, renaming, deleting.

Layout: transcripts live at ``<folder>/<name>.txt``, audio at
``<folder>/audio/<name>.wav``. Bare ``<folder>/<name>.wav`` is legacy but read.
"""

from datetime import datetime
import json
import math
import os
from pathlib import Path
import shutil
import wave

import numpy as np

from .config import AUDIO_SUBDIR, DATA_DIR, INTERNAL_TOP_DIRS
from .errors import AppError
from .filetimes import created_at
from . import screen
from .screen import screen_path_for
from .paths import (
    rel_to_data,
    resolve_in_data,
    safe_target_dir,
    sanitize_component,
    valid_component,
)
from .playback import playback_path_for


def atomic_write_text(path, text):
    """Write ``text`` to ``path`` without ever leaving a half-written file:
    a reader (or a crash) between open() and the full write would otherwise
    see truncated JSON. Write to a sibling temp file, then rename in place."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _items_in(target, rel_folder):
    """One entry per recording name in ``target``, merging its artifacts."""
    entries = list(target.iterdir())
    files = [p.name for p in entries if p.is_file()]
    txt_stems = {Path(f).stem for f in files if f.endswith(".txt") and not f.endswith(".summary.txt")}
    summary_stems = {f[:-11] for f in files if f.endswith(".summary.md")}
    meeting_json_stems = {f[:-len(".meeting.json")] for f in files if f.endswith(".meeting.json")}
    summary_json_stems = {f[:-len(".summary.json")] for f in files if f.endswith(".summary.json")}
    audio_dir = target / AUDIO_SUBDIR
    wav_map = {p.stem: p for p in audio_dir.glob("*.wav")} if audio_dir.is_dir() else {}
    legacy_wav = {
        p.name[:-4]: target / p.name
        for p in entries if p.is_file() and p.name.endswith(".wav")
    }
    wav_map = {**legacy_wav, **wav_map}

    def recorded_at(n):
        # The earliest artifact wins: a trim replaces the wav with a new file,
        # but the transcript made before it still dates the meeting.
        artifacts = [wav_map.get(n), target / f"{n}.txt", target / f"{n}.meeting.json"]
        return min(created_at(p) for p in artifacts if p is not None and p.is_file())

    return [{
        "name": n,
        # Folder the artifacts actually live in. Recordings kept in their own
        # folder are listed by their parent, so the browser cannot assume the
        # folder it is showing.
        "dir": rel_folder,
        "txt": n in txt_stems,
        "wav": n in wav_map,
        "wav_path": rel_to_data(wav_map[n]) if n in wav_map else None,
        "summary": n in summary_stems,
        "summary_path": rel_to_data(target / f"{n}.summary.md") if n in summary_stems else None,
        "segments": n in meeting_json_stems,
        "summary_json": n in summary_json_stems,
        "recorded_at": recorded_at(n),
    } for n in sorted(set(txt_stems) | set(wav_map))]


def _visible_subfolders(target):
    hidden = INTERNAL_TOP_DIRS | {AUDIO_SUBDIR}
    return sorted(
        p.name for p in target.iterdir()
        if p.is_dir() and p.name not in hidden and not p.name.startswith(".")
    )


def _same_recording_name(folder_name, item_name):
    """Whether ``folder_name`` is the folder the UI made for ``item_name``.

    Before both used "-", the file got "_" where the folder name got "-" for
    characters like "/" and ":", and the folder collapsed runs of spaces.
    """
    def canonical(name):
        return " ".join(name.replace("_", "-").split())
    return canonical(folder_name) == canonical(item_name)


def is_recording_folder(path):
    """True for a folder that is only the container of the recording it is named after.

    Every new recording gets its own folder so transcript, summary and audio
    stay together. Showing that as a folder to click through made the library
    one wrapper deep for every meeting, so such a folder is listed as the
    recording itself instead.
    """
    if not path.is_dir() or _visible_subfolders(path):
        return False
    items = _items_in(path, "")
    return len(items) == 1 and _same_recording_name(path.name, items[0]["name"])


def browse(rel_folder):
    """Folder listing merging transcripts and recordings into one item per name."""
    target = DATA_DIR if not rel_folder else resolve_in_data(rel_folder)
    if target is None or not target.is_dir():
        raise AppError("invalid path")

    items = _items_in(target, rel_folder)
    subfolders = []
    for name in _visible_subfolders(target):
        child = target / name
        child_rel = f"{rel_folder}/{name}" if rel_folder else name
        if is_recording_folder(child):
            # The folder is made just before recording and outlives a trim.
            folder_made = created_at(child)
            for item in _items_in(child, child_rel):
                item["recorded_at"] = min(item["recorded_at"], folder_made)
                items.append(item)
        else:
            subfolders.append((created_at(child), name))
    # Newest first, by when each meeting (or folder) came to be.
    items.sort(key=lambda item: (-item["recorded_at"], item["name"]))
    subfolders.sort(key=lambda entry: (-entry[0], entry[1]))
    return {"folder": rel_folder, "subfolders": [name for _, name in subfolders], "items": items}


def make_dir(folder):
    return rel_to_data(safe_target_dir(folder))


def _mutable_folder(path):
    target = resolve_in_data(path)
    if target is None or not target.is_dir() or target.resolve() == DATA_DIR.resolve():
        raise AppError("Folder not found.")
    relative = target.resolve().relative_to(DATA_DIR.resolve())
    if relative.parts[0] in INTERNAL_TOP_DIRS or relative.parts[0].startswith("."):
        raise AppError("This folder cannot be changed.")
    return target


def rename_folder(path, new_name):
    """Rename one library folder without moving it outside its parent."""
    if not valid_component(new_name):
        raise AppError("Invalid folder name.")
    source = _mutable_folder(path)
    destination = source.with_name(new_name)
    if destination.exists():
        raise AppError("Destination folder already exists.")
    source.rename(destination)
    return rel_to_data(destination)


def delete_folder(path):
    """Remove a library folder and all artifacts below it."""
    target = _mutable_folder(path)
    shutil.rmtree(target)


def read_text(path):
    target = resolve_in_data(path)
    if target is None or not target.is_file():
        raise AppError("invalid path")
    return target.read_text()


def delete_file(path):
    target = resolve_in_data(path)
    if target is None or not target.is_file():
        raise AppError("invalid path")
    target.unlink()
    if target.suffix == ".wav":
        playback_path_for(target).unlink(missing_ok=True)
    if target.name.endswith(".summary.md"):
        target.with_name(f"{target.name[:-len('.summary.md')]}.summary.json").unlink(missing_ok=True)
    elif target.suffix == ".txt":
        meeting_json_path_for(target).unlink(missing_ok=True)
    if target.suffix == ".wav":
        screen_path_for(target).unlink(missing_ok=True)
    holder = target.parent.parent if target.parent.name == AUDIO_SUBDIR else target.parent
    _prune_recording_folder(holder)


def _prune_recording_folder(path):
    """Remove a per-recording folder once its last artifact is gone.

    Without this, deleting or moving a meeting leaves an empty wrapper folder
    behind, which the library would then show as a real folder to open.
    """
    try:
        relative = path.resolve().relative_to(DATA_DIR.resolve())
    except (ValueError, OSError):
        return
    # Never touch the library root or a project folder the user made.
    if len(relative.parts) < 2 or relative.parts[0] in INTERNAL_TOP_DIRS:
        return
    if _visible_subfolders(path) or _items_in(path, ""):
        return
    if any(p.is_file() for p in path.iterdir() if p.name != AUDIO_SUBDIR):
        return
    audio = path / AUDIO_SUBDIR
    if audio.is_dir() and any(audio.iterdir()):
        return
    shutil.rmtree(path)


def audio_dir_for(folder):
    d = safe_target_dir(folder) / AUDIO_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_recording(source_wav, folder, filename):
    """Move a freshly captured/downloaded wav into the library. Returns its path."""
    name = sanitize_component(filename, datetime.now().strftime("%Y-%m-%d_%H-%M"))
    target_file = audio_dir_for(folder) / f"{name}.wav"
    if target_file.exists():
        raise AppError("target audio already exists")
    source_wav.rename(target_file)
    screen.store(target_file)
    return target_file


def transcript_path_for(wav_path):
    """Transcript sitting next to a recording's folder, not inside audio/."""
    txt_dir = wav_path.parent.parent if wav_path.parent.name == AUDIO_SUBDIR else wav_path.parent
    return txt_dir / f"{wav_path.stem}.txt"


def meeting_json_path_for(txt_path):
    """Structured segments/speakers sidecar next to a transcript."""
    return txt_path.with_name(f"{txt_path.stem}.meeting.json")


def _read_json_sidecar(path):
    """A truncated/corrupt sidecar must not permanently 500 a meeting."""
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise AppError(
            "This meeting's saved data could not be read. It may be damaged."
        ) from exc


def read_meeting(path):
    """Structured segments for a transcript, or ``{"segments": None}`` if absent."""
    txt_path = resolve_in_data(path)
    if txt_path is None or not txt_path.is_file():
        raise AppError("invalid path")
    meeting_path = meeting_json_path_for(txt_path)
    if not meeting_path.is_file():
        return {"segments": None}
    return _read_json_sidecar(meeting_path)


def read_summary_json(path):
    """Structured summary, with a Markdown fallback for older summaries."""
    txt_path = resolve_in_data(path)
    if txt_path is None or not txt_path.is_file():
        raise AppError("invalid path")
    summary_path = txt_path.with_name(f"{txt_path.stem}.summary.json")
    if summary_path.is_file():
        return _read_json_sidecar(summary_path)
    markdown_path = txt_path.with_name(f"{txt_path.stem}.summary.md")
    if markdown_path.is_file():
        return {"markdown": markdown_path.read_text()}
    return None


# Cues of one speaker join into a paragraph until a pause this long or the
# paragraph reaches PARAGRAPH_CHARS. The same rule as the meeting view's
# "Normal" grouping (PAUSE_BREAK and MAX_CHARS in meeting.js), so the text
# file reads like the screen instead of one ASR window per line.
PARAGRAPH_PAUSE_SECONDS = 2.5
PARAGRAPH_CHARS = 400


def render_transcript_text(segments):
    """Plain-text transcript: ``[SPEAKER]`` headings over paragraphs of cues."""
    blocks = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        speaker = seg.get("speaker")
        last = blocks[-1] if blocks else None
        if (
            last
            and last["speaker"] == speaker
            and float(seg["start"]) - last["end"] <= PARAGRAPH_PAUSE_SECONDS
            and len(last["text"]) < PARAGRAPH_CHARS
        ):
            last["text"] = f"{last['text']} {text}".strip()
            last["end"] = float(seg["end"])
        else:
            blocks.append({"speaker": speaker, "end": float(seg["end"]), "text": text})
    lines, last_speaker = [], None
    for block in blocks:
        if block["speaker"] and block["speaker"] != last_speaker:
            lines.append(f"\n[{block['speaker']}]")
        last_speaker = block["speaker"]
        lines.append(block["text"])
    return "\n".join(lines).strip()




def update_transcript(path, edits):
    """Persist corrections and mark the derived AI analysis as outdated."""
    txt_path = resolve_in_data(path)
    if txt_path is None or not txt_path.is_file():
        raise AppError("invalid path")
    meeting_path = meeting_json_path_for(txt_path)
    if not meeting_path.is_file():
        raise AppError("Segments are not available.")
    data = _read_json_sidecar(meeting_path)
    segments = data.get("segments") or []
    if len(edits) != len(segments):
        raise AppError("Transcript changed elsewhere. Reload before saving.")
    for segment, edit in zip(segments, edits):
        text = edit["text"].strip()
        if not text:
            raise AppError("Every transcript cue needs text.")
        segment["text"] = text
        speaker = (edit.get("speaker") or "").strip()
        segment["speaker"] = speaker or None
    data["segments"] = segments
    data["analysis_stale"] = True
    atomic_write_text(meeting_path, json.dumps(data, ensure_ascii=False, indent=2))
    txt_path.write_text(render_transcript_text(segments))
    return {"segments": segments}


def rename_speakers(path, names):
    """Rename diarized labels across every cue of one transcript at once."""
    txt_path = resolve_in_data(path)
    if txt_path is None or not txt_path.is_file():
        raise AppError("invalid path")
    meeting_path = meeting_json_path_for(txt_path)
    if not meeting_path.is_file():
        raise AppError("Segments are not available.")
    mapping = {old: new.strip() for old, new in names.items() if new and new.strip()}
    if not mapping:
        raise AppError("No speaker names were provided.")
    data = _read_json_sidecar(meeting_path)
    segments = data.get("segments") or []
    renamed = 0
    for segment in segments:
        new_name = mapping.get(segment.get("speaker"))
        if new_name and new_name != segment.get("speaker"):
            segment["speaker"] = new_name
            renamed += 1
    if renamed:
        data["segments"] = segments
        # The stored analysis quotes the old labels, so it is now out of date.
        data["analysis_stale"] = True
        atomic_write_text(meeting_path, json.dumps(data, ensure_ascii=False, indent=2))
        txt_path.write_text(render_transcript_text(segments))
    return {"segments": segments, "renamed": renamed}


# The trim view draws loudness on a dB scale, so quiet speech stays visible.
LEVEL_FLOOR_DB = -60.0
LEVEL_BUCKETS = 600
TRIM_MIN_SECONDS = 1.0


def _library_wav(path):
    target = resolve_in_data(path)
    if target is None or not target.is_file() or target.suffix.lower() != ".wav":
        raise AppError("Recording not found.")
    return target


def screen_video(path):
    """The screen video recorded together with a library wav, or None."""
    video = screen_path_for(_library_wav(path))
    return video if video.is_file() else None


def recording_levels(path):
    """Loudness over time, one 0..1 value per bucket, for the trim view.

    The louder channel wins, so a remote voice on the system track shows as
    clearly as someone speaking in the room."""
    wav = _library_wav(path)
    with wave.open(str(wav), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise AppError("This recording's audio format cannot be trimmed.")
        rate, channels, frames = handle.getframerate(), handle.getnchannels(), handle.getnframes()
        per_bucket = max(1, -(-frames // LEVEL_BUCKETS))
        levels = []
        while chunk := handle.readframes(per_bucket):
            samples = np.frombuffer(chunk, dtype="<i2").reshape(-1, channels).astype(np.float32)
            rms = float(np.sqrt(np.mean(samples * samples, axis=0)).max()) / 32768
            db = 20 * math.log10(rms + 1e-9)
            levels.append(round(min(1.0, max(0.0, 1 - db / LEVEL_FLOOR_DB)), 3))
    return {"duration": frames / rate, "levels": levels}


def _copy_frames(source, out_path, first, last):
    """Write frames ``first..last`` of an open wav to a new file, in chunks."""
    params = source.getparams()
    source.setpos(first)
    with wave.open(str(out_path), "wb") as out:
        out.setparams(params)
        remaining = last - first
        while remaining:
            chunk = source.readframes(min(remaining, params.framerate * 60))
            if not chunk:
                break
            out.writeframes(chunk)
            remaining -= len(chunk) // (params.sampwidth * params.nchannels)


def trim_recording(path, start, end):
    """Cut a recording down to ``start..end`` seconds, in place.

    An existing transcript follows the cut: cues centred outside the kept
    range go, the rest shift to the new zero. Speaker names and manual edits
    survive without transcribing again. The AI analysis is marked outdated."""
    wav = _library_wav(path)
    txt_path = transcript_path_for(wav)
    meeting_path = meeting_json_path_for(txt_path)
    meeting = None
    if meeting_path.is_file():
        meeting = _read_json_sidecar(meeting_path)
    elif txt_path.is_file():
        raise AppError("This transcript has no timings to follow the cut. Delete it first, then trim.")

    tmp = wav.with_name(f".{wav.name}.trim")
    try:
        with wave.open(str(wav), "rb") as source:
            rate, frames = source.getframerate(), source.getnframes()
            first = max(0, round(start * rate))
            last = min(frames, round(end * rate))
            if last - first < TRIM_MIN_SECONDS * rate:
                raise AppError("Keep at least one second of the recording.")
            if first == 0 and last == frames:
                raise AppError("Nothing to trim.")
            _copy_frames(source, tmp, first, last)
        os.replace(tmp, wav)
    finally:
        tmp.unlink(missing_ok=True)

    offset, kept = first / rate, (last - first) / rate
    if meeting is not None:
        segments = []
        for seg in meeting.get("segments") or []:
            if not 0 <= (seg["start"] + seg["end"]) / 2 - offset < kept:
                continue
            seg["start"] = round(max(0.0, seg["start"] - offset), 3)
            seg["end"] = round(min(kept, seg["end"] - offset), 3)
            segments.append(seg)
        meeting["segments"] = segments
        meeting["duration"] = round(kept, 2)
        meeting["analysis_stale"] = True
        atomic_write_text(meeting_path, json.dumps(meeting, ensure_ascii=False, indent=2))
        txt_path.write_text(render_transcript_text(segments))
    # Last, so a video that cannot follow still leaves audio and transcript in step.
    screen_kept = screen.trim(screen_path_for(wav), offset, offset + kept)
    return {"duration": kept, "transcript_shifted": meeting is not None, "screen_kept": screen_kept}




def move_item(folder, name, to_folder, to_name, wav_path=None):
    """Rename/move every artifact belonging to a library item together."""
    if not valid_component(name) or not valid_component(to_name):
        raise AppError("invalid name")
    src_dir = DATA_DIR if not folder else resolve_in_data(folder)
    if src_dir is None or not src_dir.is_dir():
        raise AppError("invalid path")
    dst_dir = safe_target_dir(to_folder)
    moves = []
    for suffix in (".txt", ".summary.md", ".meeting.json", ".summary.json"):
        source = src_dir / f"{name}{suffix}"
        if source.is_file():
            moves.append((source, dst_dir / f"{to_name}{suffix}", suffix.lstrip(".")))

    if wav_path:
        source = resolve_in_data(wav_path)
        allowed_audio = {src_dir / f"{name}.wav", src_dir / AUDIO_SUBDIR / f"{name}.wav"}
        if source not in allowed_audio or source is None or not source.is_file():
            raise AppError("invalid path")
        moves.append((source, dst_dir / AUDIO_SUBDIR / f"{to_name}.wav", "wav"))
        listening = playback_path_for(source)
        if listening.is_file():
            moves.append((listening, playback_path_for(dst_dir / AUDIO_SUBDIR / f"{to_name}.wav"), "playback"))

    if not moves:
        raise AppError("item not found")
    if wav_path:
        video = screen_path_for(resolve_in_data(wav_path))
        if video.is_file():
            moves.append((video, screen_path_for(dst_dir / AUDIO_SUBDIR / f"{to_name}.wav"), "screen"))
    if any(destination.exists() for _, destination, _ in moves):
        raise AppError("destination item already exists")
    # Classified before the rename: afterwards the folder no longer matches
    # the name it wrapped, so it would never look like a recording folder.
    wraps_this_item = _same_recording_name(src_dir.name, name) and is_recording_folder(src_dir)
    for _, destination, _ in moves:
        destination.parent.mkdir(parents=True, exist_ok=True)
    for source, destination, _ in moves:
        source.rename(destination)
    final_dir = dst_dir
    if src_dir != dst_dir:
        _prune_recording_folder(src_dir)
    elif wraps_this_item:
        # A meeting kept in its own folder is shown by that folder's name;
        # renaming the meeting has to carry the wrapper with it.
        renamed_dir = src_dir.with_name(to_name)
        if not renamed_dir.exists():
            src_dir.rename(renamed_dir)
            final_dir = renamed_dir
    return {"moved": [kind for _, _, kind in moves], "folder": rel_to_data(final_dir)}

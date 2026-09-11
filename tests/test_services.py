from types import SimpleNamespace

import httpx
import json

from transcriber import asr
from transcriber.errors import AppError
from transcriber.summary import summarize


def test_asr_falls_back_to_local_when_remote_transcription_fails(monkeypatch, tmp_path):
    wav = tmp_path / "meeting.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setattr(asr, "remote_status", lambda: {"available": True})
    monkeypatch.setattr(asr, "_remote_transcribe", lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("down")))
    expected = asr.Transcript([asr.Segment(0, 1, "hello")], 1, "local")
    monkeypatch.setattr(asr, "_local_transcribe", lambda *args: expected)
    assert asr.transcribe(wav, "en").backend == "local"

def test_transcribe_enhances_audio_before_sending_it_to_the_model(monkeypatch, tmp_path):
    wav = tmp_path / "meeting.wav"
    wav.write_bytes(b"original")
    ffmpeg_args = []

    def enhance(args, **kwargs):
        ffmpeg_args.extend(args)
        from pathlib import Path
        Path(args[-1]).write_bytes(b"enhanced")
        return SimpleNamespace(returncode=0, stderr="")

    seen = {}
    expected = asr.Transcript([asr.Segment(0, 1, "hello")], 1, "spark")
    monkeypatch.setattr(asr.subprocess, "run", enhance)
    monkeypatch.setattr(asr, "remote_status", lambda: {"available": True})
    monkeypatch.setattr(
        asr, "_remote_transcribe",
        lambda path, *args, **kwargs: (seen.update(path=path, content=path.read_bytes()), expected)[1],
    )

    assert asr.transcribe(wav, "cs") == expected
    assert seen["path"].name == "enhanced.wav"
    assert seen["content"] == b"enhanced"
    assert asr.MODEL_AUDIO_FILTER in ffmpeg_args
    assert wav.read_bytes() == b"original"


def test_local_transcription_always_pins_a_language(monkeypatch, tmp_path):
    """Auto-detection drifts per VAD window: Czech audio came back part Slovak,
    part English. The language must reach the model on every local pass."""
    import wave

    wav = tmp_path / "meeting.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x01" * 16000)
    seen = []

    class Model:
        def recognize(self, path, **kwargs):
            seen.append(kwargs)
            return iter([SimpleNamespace(start=0.0, end=1.0, text="ahoj")])

    monkeypatch.setattr(asr, "get_local_asr", lambda: Model())

    assert asr._local_transcribe(wav, "cs", lambda *_: None).duration == 1.0
    asr._local_transcribe(wav, None, lambda *_: None)

    assert [kwargs["language"] for kwargs in seen] == ["cs", asr.LOCAL_ASR_DEFAULT_LANGUAGE]
    assert all(kwargs["pnc"] is True for kwargs in seen)


def test_summarize_returns_structured_data_with_evidence_timestamps(monkeypatch):
    seen = {}
    llm_json = json.dumps({
        "summary": "Team discussed launch.",
        "chapters": [{"title": "Launch", "summary": "Deadline agreed.", "start_index": 0, "end_index": 1}],
        "decisions": [{"text": "Ship Friday", "rationale": "The release is ready.", "alternatives": [], "evidence_indexes": [1]}],
        "action_items": [{"text": "Write docs", "owner": "Jan", "deadline": "Friday", "priority": "high", "evidence_indexes": [0]}],
        "open_questions": [], "risks": [], "speaker_contributions": [], "follow_up": "Jan will publish docs.", "changes_since_last": [],
    })
    def fake_post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": llm_json}}]},
        )
    monkeypatch.setattr("transcriber.summary.GPT_OSS_API_KEY", "test-token")
    monkeypatch.setattr("transcriber.summary.httpx.post", fake_post)

    segments = [
        {"start": 0.0, "end": 5.0, "speaker": "Jan", "text": "We need docs."},
        {"start": 5.0, "end": 10.0, "speaker": "Petr", "text": "Let's ship Friday."},
    ]
    result = summarize("We need docs.\nLet's ship Friday.", "Acme", segments)

    assert result["summary"] == "Team discussed launch."
    assert result["decisions"] == [{
        "text": "Ship Friday", "rationale": "The release is ready.", "alternatives": [],
        "evidence": [{"start": 5.0, "end": 10.0, "speaker": "Petr"}],
    }]
    assert result["action_items"] == [{
        "text": "Write docs", "owner": "Jan", "deadline": "Friday", "priority": "high",
        "evidence": [{"start": 0.0, "end": 5.0, "speaker": "Jan"}],
    }]
    assert result["chapters"][0]["evidence"][1]["start"] == 5.0
    assert seen["json"]["stream"] is False
    assert "Project: Acme" in seen["json"]["messages"][1]["content"]
    assert "[1] 00:05 Petr:" in seen["json"]["messages"][1]["content"]
    assert seen["headers"] == {"Authorization": "Bearer test-token"}
    assert seen["json"]["response_format"]["json_schema"]["name"] == "meeting_intelligence"


def test_summarize_falls_back_to_raw_text_on_unparseable_response(monkeypatch):
    def fake_post(url, **kwargs):
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": "not json at all"}}]},
        )
    monkeypatch.setattr("transcriber.summary.httpx.post", fake_post)
    result = summarize("Hello")
    assert result == {
        "summary": "not json at all", "chapters": [], "decisions": [], "action_items": [],
        "open_questions": [], "risks": [], "speaker_contributions": [], "follow_up": "", "changes_since_last": [],
    }


def test_render_markdown_omits_empty_sections():
    from transcriber.summary import render_markdown

    markdown = render_markdown({
        "summary": "Short recap.", "chapters": [], "decisions": [],
        "action_items": [{"text": "Follow up", "owner": None, "deadline": None}],
        "open_questions": [], "risks": [], "changes_since_last": [], "follow_up": "",
    })
    assert markdown == "## Summary\nShort recap.\n\n## Action items\n- Follow up"
    assert "Decisions" not in markdown
    assert "Open questions" not in markdown


def test_resolve_in_data_rejects_sibling_with_shared_prefix(monkeypatch, tmp_path):
    from transcriber import paths

    data = tmp_path / "data"
    sibling = tmp_path / "data2"
    data.mkdir()
    sibling.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", data)
    assert paths.resolve_in_data("../data2/private.txt") is None
    assert paths.resolve_in_data("project/meeting.txt") == data / "project/meeting.txt"


def test_move_item_moves_all_artifacts_or_none(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    source = tmp_path / "source"
    (source / "audio").mkdir(parents=True)
    (source / "meeting.txt").write_text("transcript")
    (source / "meeting.summary.md").write_text("summary")
    (source / "audio" / "meeting.wav").write_bytes(b"wav")

    result = library.move_item("source", "meeting", "destination", "renamed", "source/audio/meeting.wav")
    assert result["moved"] == ["txt", "summary.md", "wav"]
    assert result["folder"] == "destination"
    assert (tmp_path / "destination" / "renamed.txt").read_text() == "transcript"
    assert (tmp_path / "destination" / "renamed.summary.md").read_text() == "summary"
    assert (tmp_path / "destination" / "audio" / "renamed.wav").read_bytes() == b"wav"


def test_move_item_rejects_invalid_name_without_touching_files(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    (tmp_path / "meeting.txt").write_text("transcript")
    try:
        library.move_item("", "meeting", "", "../escaped")
    except AppError:
        pass
    else:
        raise AssertionError("invalid destination name was accepted")
    assert (tmp_path / "meeting.txt").read_text() == "transcript"


def test_store_recording_preserves_existing_audio(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    source = tmp_path / "incoming.wav"
    source.write_bytes(b"new")
    target = tmp_path / "project" / "audio"
    target.mkdir(parents=True)
    (target / "meeting.wav").write_bytes(b"old")
    try:
        library.store_recording(source, "project", "meeting")
    except AppError:
        pass
    else:
        raise AssertionError("existing audio was overwritten")
    assert source.read_bytes() == b"new"
    assert (target / "meeting.wav").read_bytes() == b"old"


def test_progress_rejects_parallel_transcription():
    from transcriber.progress import Progress

    progress = Progress()
    assert progress.begin(message="first") is True
    assert progress.begin(message="second") is False
    progress.finish(stage="done")
    assert progress.begin(message="next") is True


def test_recorder_rejects_second_start(monkeypatch, tmp_path):
    from transcriber.recorder import Recorder

    recorder = Recorder(tmp_path / "recording.wav")
    recorder.proc = object()
    try:
        recorder.start()
    except AppError:
        pass
    else:
        raise AssertionError("second recording was accepted")


def test_recorder_sets_and_clears_started_at_across_start_stop(monkeypatch, tmp_path):
    from transcriber import recorder as recorder_module

    wav_path = tmp_path / "recording.wav"
    recorder = recorder_module.Recorder(wav_path)
    pactl_calls = []
    sources = [{"name": "usb-microphone", "description": "USB Microphone", "properties": {}}]

    def pactl(*args):
        pactl_calls.append(args)
        if args == ("get-default-source",):
            return sources[0]["name"]
        if args == ("-f", "json", "list", "sources"):
            return json.dumps(sources)
        if args == ("-f", "json", "list", "cards"):
            return json.dumps([])
        return "42"

    monkeypatch.setattr(recorder_module, "pactl", pactl)
    monkeypatch.setattr(
        recorder_module.subprocess, "Popen",
        lambda *args, **kwargs: SimpleNamespace(send_signal=lambda sig: None, wait=lambda timeout=None: wav_path.write_bytes(b"RIFF")),
    )
    monkeypatch.setattr(recorder_module.subprocess, "run", lambda *args, **kwargs: None)

    assert recorder.started_at is None
    recorder.start("usb-microphone")
    assert recorder.started_at is not None
    assert any("source=@DEFAULT_SINK@.monitor" in call for args in pactl_calls for call in args)
    recorder.stop()
    assert recorder.started_at is None


def test_microphones_excludes_output_monitors_and_marks_default(monkeypatch):
    from transcriber import recorder as recorder_module

    sources = [
        {"name": "speaker.monitor", "description": "Monitor", "properties": {"device.class": "monitor"}},
        {"name": "built-in-mic", "description": "Digital Microphone", "properties": {"device.class": "sound"}},
        {"name": "usb-mic", "description": "USB Microphone", "properties": {"device.class": "sound"}},
    ]
    monkeypatch.setattr(
        recorder_module,
        "pactl",
        lambda *args: "usb-mic" if args == ("get-default-source",) else json.dumps(sources),
    )

    assert recorder_module.microphones() == [
        {"id": "built-in-mic", "label": "Digital Microphone", "default": False,
         "available": True, "bluetooth": False, "note": None},
        {"id": "usb-mic", "label": "USB Microphone", "default": True,
         "available": True, "bluetooth": False, "note": None},
    ]


def test_recorder_auto_stops_after_max_duration(monkeypatch, tmp_path):
    import time as time_module

    from transcriber import recorder as recorder_module

    wav_path = tmp_path / "recording.wav"
    recorder = recorder_module.Recorder(wav_path)
    monkeypatch.setattr(recorder_module, "MAX_RECORDING_SECONDS", 0.05)
    def pactl(*args):
        if args == ("get-default-source",):
            return "default-microphone"
        if args == ("-f", "json", "list", "sources"):
            return json.dumps([{"name": "default-microphone", "description": "Default", "properties": {}}])
        if args == ("-f", "json", "list", "cards"):
            return json.dumps([])
        return "42"

    monkeypatch.setattr(recorder_module, "pactl", pactl)
    monkeypatch.setattr(
        recorder_module.subprocess, "Popen",
        lambda *args, **kwargs: SimpleNamespace(send_signal=lambda sig: None, wait=lambda timeout=None: wav_path.write_bytes(b"RIFF")),
    )
    monkeypatch.setattr(recorder_module.subprocess, "run", lambda *args, **kwargs: None)

    recorder.start()
    assert recorder.is_recording is True

    deadline = time_module.time() + 2
    while recorder.is_recording and time_module.time() < deadline:
        time_module.sleep(0.01)

    assert recorder.is_recording is False
    assert wav_path.exists()




def test_start_reports_microphone_only_fallback(monkeypatch, tmp_path):
    from transcriber.routes import recording as recording_module

    calls = []
    fake_recorder = SimpleNamespace(
        wav_path=tmp_path / "current.wav",
        system_audio=False,
        start=lambda microphone, output: calls.append((microphone, output)),
    )
    monkeypatch.setattr(recording_module, "recorder", fake_recorder)
    monkeypatch.setattr(recording_module, "live", SimpleNamespace(start=lambda *args, **kwargs: calls.append(args)))

    assert recording_module.start() == {"ok": True, "system_audio": False}
    assert calls[0] == ("", "")


def test_recording_cancel_discards_audio_without_saving(monkeypatch, tmp_path):
    from transcriber.routes import recording as recording_module

    wav_path = tmp_path / "current.wav"
    wav_path.write_bytes(b"RIFF")
    monkeypatch.setattr(recording_module, "recorder", SimpleNamespace(stop=lambda: True))
    monkeypatch.setattr(recording_module, "WAV_PATH", wav_path)
    monkeypatch.setattr(
        recording_module, "store_recording",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cancel must not persist the recording")),
    )

    assert recording_module.cancel() == {"ok": True}
    assert not wav_path.exists()


def test_recording_cancel_is_noop_when_nothing_recording(monkeypatch, tmp_path):
    from transcriber.routes import recording as recording_module

    wav_path = tmp_path / "current.wav"
    monkeypatch.setattr(recording_module, "recorder", SimpleNamespace(stop=lambda: False))
    monkeypatch.setattr(recording_module, "WAV_PATH", wav_path)

    assert recording_module.cancel() == {"ok": True}


def test_render_text_matches_legacy_single_speaker_format():
    from transcriber.pipeline import render_text

    segments = [asr.Segment(0, 1, " Hello "), asr.Segment(1, 2, "world.")]
    assert render_text(segments) == "Hello\nworld."


def test_render_text_matches_legacy_multi_speaker_format():
    from transcriber.pipeline import render_text

    segments = [
        asr.Segment(0, 1, "Hi.", speaker="SPEAKER_00"),
        asr.Segment(1, 2, "Hi there.", speaker="SPEAKER_01"),
        asr.Segment(2, 3, "How are you?", speaker="SPEAKER_01"),
    ]
    assert render_text(segments) == "[SPEAKER_00]\nHi.\n\n[SPEAKER_01]\nHi there.\nHow are you?"


def test_run_pipeline_writes_meeting_json_with_segments(monkeypatch, tmp_path):
    from transcriber import pipeline as pipeline_module

    wav_path = tmp_path / "meeting.wav"
    txt_path = tmp_path / "meeting.txt"
    monkeypatch.setattr(pipeline_module, "rel_to_data", lambda p: str(p))
    monkeypatch.setattr(
        pipeline_module, "transcribe",
        lambda *a, **k: asr.Transcript([asr.Segment(0, 1, "hello")], 1, "local"),
    )

    pipeline_module.run_pipeline(wav_path, txt_path, "en", num_speakers=1)

    meeting_json = pipeline_module.meeting_json_path_for(txt_path)
    assert meeting_json.is_file()
    data = json.loads(meeting_json.read_text())
    assert data["backend"] == "local"
    assert data["language"] == "en"
    assert data["num_speakers"] == 1
    assert data["segments"] == [{"start": 0, "end": 1, "text": "hello", "speaker": None, "channel": None}]
    assert txt_path.read_text() == "hello"


def test_diarize_loader_explains_missing_huggingface_token(monkeypatch):
    from transcriber import models

    monkeypatch.setattr(
        models,
        "get_connection",
        lambda name: {"local_model": "pyannote/speaker-diarization-3.1", "hf_token": ""},
    )
    try:
        models.get_diarize_pipeline()
    except RuntimeError as exc:
        assert "HuggingFace" in str(exc)
        assert "pyannote" in str(exc)
    else:
        raise AssertionError("diarization loaded without a HuggingFace token")


def test_run_pipeline_falls_back_when_diarization_is_unavailable(monkeypatch, tmp_path):
    from transcriber import pipeline as pipeline_module

    wav_path = tmp_path / "meeting.wav"
    txt_path = tmp_path / "meeting.txt"
    monkeypatch.setattr(pipeline_module, "rel_to_data", lambda p: str(p))
    monkeypatch.setattr(
        pipeline_module, "transcribe",
        lambda *a, **k: asr.Transcript([asr.Segment(0, 1, "hello")], 1, "local"),
    )
    monkeypatch.setattr(
        pipeline_module, "label_speakers",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gated model")),
    )

    pipeline_module.run_pipeline(wav_path, txt_path, "en", num_speakers=2)

    state = pipeline_module.progress.snapshot()
    assert state["stage"] == "done"
    assert "without speaker separation" in state["warning"]
    assert txt_path.read_text() == "hello"
    data = json.loads(pipeline_module.meeting_json_path_for(txt_path).read_text())
    assert data["segments"] == [{"start": 0, "end": 1, "text": "hello", "speaker": None, "channel": None}]


def test_rerun_diarization_relabels_existing_segments_without_asr(monkeypatch, tmp_path):
    from transcriber import pipeline as pipeline_module
    from transcriber.progress import Progress

    wav_path = tmp_path / "meeting.wav"
    txt_path = tmp_path / "meeting.txt"
    wav_path.write_bytes(b"RIFF")
    txt_path.write_text("hello")
    meeting_path = pipeline_module.meeting_json_path_for(txt_path)
    meeting_path.write_text(json.dumps({
        "backend": "spark",
        "asr": {"backend": "spark", "model": "whisper-large"},
        "segments": [{"start": 0, "end": 1, "text": "hello", "speaker": None}],
    }))
    monkeypatch.setattr(pipeline_module, "rel_to_data", lambda path: str(path))
    monkeypatch.setattr(pipeline_module, "progress", Progress())
    monkeypatch.setattr(
        pipeline_module,
        "label_speakers",
        lambda _wav, segments, _count: pipeline_module.DiarizationResult(
            [
                asr.Segment(segment.start, segment.end, segment.text, "Eva")
                for segment in segments
            ],
            "spark",
            "pyannote-test",
        ),
    )

    pipeline_module.rerun_diarization(wav_path, txt_path, 2)

    result = json.loads(meeting_path.read_text())
    assert txt_path.read_text() == "[Eva]\nhello"
    assert result["asr"] == {"backend": "spark", "model": "whisper-large"}
    assert result["segments"][0]["speaker"] == "Eva"
    assert result["diarization"]["applied"] is True
    assert pipeline_module.progress.snapshot()["message"] == "Speakers were recognized on the remote engine."


def test_label_speakers_prefers_spark_diarizer(monkeypatch, tmp_path):
    from transcriber import pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module,
        "get_connection",
        lambda name: {"endpoint": "http://spark/diarize", "local_model": "pyannote-local"},
    )
    monkeypatch.setattr(
        pipeline_module,
        "_remote_speaker_turns",
        lambda *_args: (
            [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_01"}],
            "pyannote-spark",
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "_local_speaker_turns",
        lambda *_args: (_ for _ in ()).throw(AssertionError("local fallback ran")),
    )

    result = pipeline_module.label_speakers(
        tmp_path / "meeting.wav",
        [asr.Segment(0.0, 1.0, "hello")],
        2,
    )

    assert result.backend == "spark"
    assert result.model == "pyannote-spark"
    assert result.segments[0].speaker == "SPEAKER_01"


def test_label_speakers_falls_back_locally_when_spark_fails(monkeypatch, tmp_path):
    from transcriber import pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module,
        "get_connection",
        lambda name: {"endpoint": "http://spark/diarize", "local_model": "pyannote-local"},
    )
    monkeypatch.setattr(
        pipeline_module,
        "_remote_speaker_turns",
        lambda *_args: (_ for _ in ()).throw(httpx.ConnectError("down")),
    )
    monkeypatch.setattr(
        pipeline_module,
        "_local_speaker_turns",
        lambda *_args: [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"}],
    )

    result = pipeline_module.label_speakers(
        tmp_path / "meeting.wav",
        [asr.Segment(0.0, 1.0, "hello")],
        2,
    )

    assert result.backend == "local"
    assert result.segments[0].speaker == "SPEAKER_00"


def test_browse_reports_segments_and_summary_json_flags(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    (tmp_path / "meeting.txt").write_text("hi")
    (tmp_path / "meeting.meeting.json").write_text("{}")
    (tmp_path / "meeting.summary.json").write_text("{}")

    items = {item["name"]: item for item in library.browse("")["items"]}
    assert items["meeting"]["segments"] is True
    assert items["meeting"]["summary_json"] is True


def test_move_item_moves_meeting_and_summary_json_sidecars(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    (tmp_path / "meeting.txt").write_text("transcript")
    (tmp_path / "meeting.meeting.json").write_text("{}")
    (tmp_path / "meeting.summary.json").write_text("{}")

    result = library.move_item("", "meeting", "", "renamed")
    assert set(result["moved"]) == {"txt", "meeting.json", "summary.json"}
    assert (tmp_path / "renamed.meeting.json").is_file()
    assert (tmp_path / "renamed.summary.json").is_file()


def test_rename_and_delete_folder_preserve_containment(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    source = tmp_path / "project" / "old" / "audio"
    source.mkdir(parents=True)
    (source / "meeting.wav").write_bytes(b"wav")

    assert library.rename_folder("project/old", "renamed") == "project/renamed"
    assert not (tmp_path / "project" / "old").exists()
    assert (tmp_path / "project" / "renamed" / "audio" / "meeting.wav").is_file()

    library.delete_folder("project/renamed")
    assert not (tmp_path / "project" / "renamed").exists()


def test_projects_excludes_internal_audio_folder(monkeypatch, tmp_path):
    from transcriber.routes import workflow

    monkeypatch.setattr(workflow, "DATA_DIR", tmp_path)
    for name in ("Client", "audio", "_scratch", ".cache"):
        (tmp_path / name).mkdir()

    assert workflow.projects() == {"projects": ["Client"]}


def test_read_meeting_returns_none_segments_when_absent(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    (tmp_path / "meeting.txt").write_text("hi")

    assert library.read_meeting("meeting.txt") == {"segments": None}


def test_read_meeting_returns_parsed_segments(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    (tmp_path / "meeting.txt").write_text("hi")
    (tmp_path / "meeting.meeting.json").write_text(json.dumps({
        "version": 1, "segments": [{"start": 0, "end": 1, "text": "hi", "speaker": None}],
    }))

    result = library.read_meeting("meeting.txt")
    assert result["segments"] == [{"start": 0, "end": 1, "text": "hi", "speaker": None}]


def test_read_summary_json_returns_none_when_absent(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    (tmp_path / "meeting.txt").write_text("hi")

    assert library.read_summary_json("meeting.txt") is None


def test_read_summary_json_falls_back_to_existing_markdown(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    (tmp_path / "meeting.txt").write_text("hi")
    (tmp_path / "meeting.summary.md").write_text("## Summary\nHotovo.")

    assert library.read_summary_json("meeting.txt") == {"markdown": "## Summary\nHotovo."}


def test_update_transcript_marks_existing_brief_as_stale(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    (tmp_path / "meeting.txt").write_text("stale")
    (tmp_path / "meeting.meeting.json").write_text(json.dumps({
        "version": 1,
        "segments": [
            {"start": 0, "end": 1, "text": "Hi.", "speaker": "SPEAKER_00"},
            {"start": 1, "end": 2, "text": "Hello.", "speaker": "SPEAKER_01"},
        ],
    }))
    (tmp_path / "meeting.summary.md").write_text("stale brief")
    (tmp_path / "meeting.summary.json").write_text("{}")

    result = library.update_transcript("meeting.txt", [
        {"text": "Ahoj.", "speaker": "Jan"},
        {"text": "Nazdar.", "speaker": None},
    ])

    assert result["segments"] == [
        {"start": 0, "end": 1, "text": "Ahoj.", "speaker": "Jan"},
        {"start": 1, "end": 2, "text": "Nazdar.", "speaker": None},
    ]
    assert (tmp_path / "meeting.txt").read_text() == "[Jan]\nAhoj.\nNazdar."
    assert json.loads((tmp_path / "meeting.meeting.json").read_text())["analysis_stale"] is True
    assert (tmp_path / "meeting.summary.md").read_text() == "stale brief"
    assert (tmp_path / "meeting.summary.json").read_text() == "{}"


def test_rename_speakers_relabels_every_cue_of_that_speaker(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    (tmp_path / "meeting.txt").write_text("old")
    (tmp_path / "meeting.meeting.json").write_text(json.dumps({
        "version": 1,
        "segments": [
            {"start": 0, "end": 1, "text": "Hi.", "speaker": "SPEAKER_00"},
            {"start": 1, "end": 2, "text": "Hello.", "speaker": "SPEAKER_01"},
            {"start": 2, "end": 3, "text": "Again.", "speaker": "SPEAKER_00"},
        ],
    }))

    result = library.rename_speakers("meeting.txt", {"SPEAKER_00": " Jan ", "SPEAKER_01": ""})

    assert [segment["speaker"] for segment in result["segments"]] == ["Jan", "SPEAKER_01", "Jan"]
    assert result["renamed"] == 2
    assert (tmp_path / "meeting.txt").read_text() == (
        "[Jan]\nHi.\n\n[SPEAKER_01]\nHello.\n\n[Jan]\nAgain."
    )
    assert json.loads((tmp_path / "meeting.meeting.json").read_text())["analysis_stale"] is True


def test_delete_transcript_removes_structured_meeting_sidecar(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    (tmp_path / "meeting.txt").write_text("hi")
    (tmp_path / "meeting.meeting.json").write_text("{}")

    library.delete_file("meeting.txt")

    assert not (tmp_path / "meeting.txt").exists()
    assert not (tmp_path / "meeting.meeting.json").exists()


def test_delete_summary_removes_structured_summary_sidecar(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    (tmp_path / "meeting.summary.md").write_text("summary")
    (tmp_path / "meeting.summary.json").write_text("{}")

    library.delete_file("meeting.summary.md")

    assert not (tmp_path / "meeting.summary.md").exists()
    assert not (tmp_path / "meeting.summary.json").exists()




def test_upload_converts_arbitrary_audio_and_stores_it(monkeypatch, tmp_path):
    import subprocess
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from transcriber import library, paths
    from transcriber.routes import upload

    src = tmp_path / "talk.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(src)],
        check=True, capture_output=True,
    )
    scratch = tmp_path / "_scratch"
    scratch.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(upload, "SCRATCH_DIR", scratch)
    monkeypatch.setattr(upload, "WAV_PATH", scratch / "current.wav")
    monkeypatch.setattr(upload, "clear_scratch", lambda: None)

    app = FastAPI()
    app.include_router(upload.router)
    with TestClient(app) as client:
        r = client.post(
            "/upload?folder=Acme&filename=Porada&ext=mp3", content=src.read_bytes()
        )
    assert r.json()["path"] == "Acme/audio/Porada.wav"
    assert (tmp_path / "Acme/audio/Porada.wav").stat().st_size > 1000
    assert not list(scratch.glob("upload.*"))


def test_remote_transcription_stitches_chunk_timestamps(monkeypatch, tmp_path):
    from pathlib import Path

    monkeypatch.setattr(asr, "_mp3_chunks", lambda wav, out: [Path("a.mp3"), Path("b.mp3")])
    bodies = iter([
        {"duration": 900.0, "segments": [{"start": 0, "end": 5, "text": "první"}]},
        {"duration": 300.0, "segments": [{"start": 10, "end": 15, "text": "druhý"}]},
    ])
    monkeypatch.setattr(asr, "_post_audio", lambda path, lang, **kwargs: (
        [asr.Segment(float(s["start"]), float(s["end"]), s["text"]) for s in next(bodies)["segments"]],
        900.0 if path.name == "a.mp3" else 300.0,
    ))

    result = asr._remote_transcribe(tmp_path / "m.wav", "cs", lambda seg, total: None)

    assert [(s.start, s.end, s.text) for s in result.segments] == [
        (0.0, 5.0, "první"), (910.0, 915.0, "druhý"),
    ]
    assert result.duration == 1200.0
    assert result.backend == "spark"


def test_remote_error_body_under_http_200_falls_back_to_local(monkeypatch, tmp_path):
    wav = tmp_path / "m.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setattr(asr, "remote_status", lambda: {"available": True})
    monkeypatch.setattr(asr, "_mp3_chunks", lambda w, out: [wav])
    monkeypatch.setattr(asr.httpx, "post", lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"error": {"message": "Please install vllm[audio] for audio support"}},
    ))
    monkeypatch.setattr(asr, "_local_transcribe", lambda *a: asr.Transcript([], 0, "local"))

    assert asr.transcribe(wav, "cs").backend == "local"


def test_meeting_json_records_engine_provenance(monkeypatch, tmp_path):
    from transcriber import pipeline

    monkeypatch.setattr(pipeline, "transcribe_segments",
                        lambda wav, lang, cap: asr.Transcript([asr.Segment(0, 3, "ahoj")], 3.0, "local"))
    txt = tmp_path / "m.txt"
    pipeline.run_pipeline(tmp_path / "m.wav", txt, "cs", 1)

    meta = json.loads((tmp_path / "m.meeting.json").read_text())
    assert meta["asr"] == {
        "backend": "local", "where": pipeline.LOCAL_LABEL,
        "model": pipeline.LOCAL_ASR_MODEL, "device": pipeline.LOCAL_ASR_DEVICE,
    }
    assert meta["diarization"]["applied"] is False
    assert meta["duration"] == 3.0 and meta["created_at"]


def test_preferences_are_merged_and_persisted(monkeypatch, tmp_path):
    from transcriber import preferences

    monkeypatch.setattr(preferences, "PREFERENCES_PATH", tmp_path / "preferences.json")
    assert preferences.get_preferences()["recording"]["live_enabled"] is False

    saved = preferences.save_preferences({
        "recording": {"language": "en", "live_enabled": False},
        "brief": {"detail": "detailed", "sections": {"risks": False}},
    })

    assert saved["recording"]["language"] == "en"
    assert saved["recording"]["live_enabled"] is False
    assert saved["recording"]["speaker_count"] == ""
    assert preferences.get_preferences()["brief"]["sections"]["risks"] is False


def test_brief_preferences_remove_disabled_sections():
    from transcriber.routes.workflow import _filter_brief

    data = {"summary": "recap", "risks": [{"text": "risk"}], "follow_up": "send this", "changes_since_last": ["changed"]}
    preferences = {"brief": {"compare_previous": False, "sections": {"risks": False, "follow_up": False}}}

    assert _filter_brief(data, preferences) == {
        "summary": "recap", "risks": [], "follow_up": "", "changes_since_last": [],
    }

def test_summary_generation_returns_immediately_and_exposes_progress(monkeypatch, tmp_path):
    import threading
    import time
    from transcriber import library, paths
    from transcriber.progress import Progress
    from transcriber.routes import workflow
    from transcriber.schemas import SummaryReq

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(workflow, "progress", Progress())
    (tmp_path / "meeting.txt").write_text("Ahoj.")
    started = threading.Event()
    release = threading.Event()

    def slow_summary(*args):
        started.set()
        release.wait(1)
        return {
            "summary": "Hotovo.", "chapters": [], "decisions": [], "action_items": [],
            "open_questions": [], "risks": [], "speaker_contributions": [],
            "follow_up": "", "changes_since_last": [],
        }

    monkeypatch.setattr(workflow, "summarize", slow_summary)

    assert workflow.create_summary(SummaryReq(path="meeting.txt")) == {"ok": True}
    assert started.wait(.2)
    assert workflow.progress.snapshot()["stage"] == "analyzing"
    assert workflow.progress.snapshot()["source_path"] == "meeting.txt"

    release.set()
    for _ in range(100):
        if workflow.progress.snapshot()["stage"] == "done":
            break
        time.sleep(.01)
    else:
        raise AssertionError("summary worker did not finish")
    assert (tmp_path / "meeting.summary.json").is_file()

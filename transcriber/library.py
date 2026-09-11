"""The /data library: browsing, storing recordings, renaming, deleting.

Layout: transcripts live at ``<folder>/<name>.txt``, audio at
``<folder>/audio/<name>.wav``. Bare ``<folder>/<name>.wav`` is legacy but read.
"""

from datetime import datetime
import json
import os
from pathlib import Path
import shutil

from .config import AUDIO_SUBDIR, DATA_DIR, INTERNAL_TOP_DIRS
from .errors import AppError
from .paths import (
    rel_to_data,
    resolve_in_data,
    safe_target_dir,
    sanitize_component,
    valid_component,
)


def atomic_write_text(path, text):
    """Write ``text`` to ``path`` without ever leaving a half-written file:
    a reader (or a crash) between open() and the full write would otherwise
    see truncated JSON. Write to a sibling temp file, then rename in place."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def browse(rel_folder):
    """Folder listing merging transcripts and recordings into one item per name."""
    target = DATA_DIR if not rel_folder else resolve_in_data(rel_folder)
    if target is None or not target.is_dir():
        raise AppError("invalid path")

    entries = list(target.iterdir())
    hidden = INTERNAL_TOP_DIRS | {AUDIO_SUBDIR}
    subfolders = sorted(
        p.name for p in entries
        if p.is_dir() and p.name not in hidden and not p.name.startswith(".")
    )
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

    names = sorted(set(txt_stems) | set(wav_map))
    items = [{
        "name": n,
        "txt": n in txt_stems,
        "wav": n in wav_map,
        "wav_path": rel_to_data(wav_map[n]) if n in wav_map else None,
        "summary": n in summary_stems,
        "summary_path": rel_to_data(target / f"{n}.summary.md") if n in summary_stems else None,
        "segments": n in meeting_json_stems,
        "summary_json": n in summary_json_stems,
    } for n in names]
    return {"folder": rel_folder, "subfolders": subfolders, "items": items}


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
    if target.name.endswith(".summary.md"):
        target.with_name(f"{target.name[:-len('.summary.md')]}.summary.json").unlink(missing_ok=True)
    elif target.suffix == ".txt":
        meeting_json_path_for(target).unlink(missing_ok=True)


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


# ponytail: duplicates pipeline.render_text's speaker-heading logic, on dicts
# instead of Segment dataclasses. Sharing it would mean library.py importing
# from pipeline.py, which already imports from library.py (meeting_json_path_for)
# -> circular import. Keep both; they're 6 lines and independently tested.
def _render_segments_text(segments):
    if not any(seg.get("speaker") for seg in segments):
        return "\n".join((seg.get("text") or "").strip() for seg in segments).strip()
    lines, last_speaker = [], None
    for seg in segments:
        speaker = seg.get("speaker")
        if speaker and speaker != last_speaker:
            lines.append(f"\n[{speaker}]")
        last_speaker = speaker
        lines.append((seg.get("text") or "").strip())
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
    txt_path.write_text(_render_segments_text(segments))
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
        txt_path.write_text(_render_segments_text(segments))
    return {"segments": segments, "renamed": renamed}




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

    if not moves:
        raise AppError("item not found")
    if any(destination.exists() for _, destination, _ in moves):
        raise AppError("destination item already exists")
    for _, destination, _ in moves:
        destination.parent.mkdir(parents=True, exist_ok=True)
    for source, destination, _ in moves:
        source.rename(destination)
    return [kind for _, _, kind in moves]

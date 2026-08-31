"""The /data library: browsing, storing recordings, renaming, deleting.

Layout: transcripts live at ``<folder>/<name>.txt``, audio at
``<folder>/audio/<name>.wav``. Bare ``<folder>/<name>.wav`` is legacy but read.
"""

from datetime import datetime
from pathlib import Path

from .config import AUDIO_SUBDIR, DATA_DIR, INTERNAL_TOP_DIRS
from .errors import AppError
from .paths import rel_to_data, resolve_in_data, safe_target_dir, sanitize_component


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
    } for n in names]
    return {"folder": rel_folder, "subfolders": subfolders, "items": items}


def make_dir(folder):
    return rel_to_data(safe_target_dir(folder))


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


def audio_dir_for(folder):
    d = safe_target_dir(folder) / AUDIO_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_recording(source_wav, folder, filename):
    """Move a freshly captured/downloaded wav into the library. Returns its path."""
    name = sanitize_component(filename, datetime.now().strftime("%Y-%m-%d_%H-%M"))
    target_file = audio_dir_for(folder) / f"{name}.wav"
    source_wav.rename(target_file)
    return target_file


def transcript_path_for(wav_path):
    """Transcript sitting next to a recording's folder, not inside audio/."""
    txt_dir = wav_path.parent.parent if wav_path.parent.name == AUDIO_SUBDIR else wav_path.parent
    return txt_dir / f"{wav_path.stem}.txt"


def move_item(folder, name, to_folder, to_name, wav_path=None):
    """Rename/move a transcript and its recording together."""
    src_dir = DATA_DIR if not folder else resolve_in_data(folder)
    if src_dir is None:
        raise AppError("invalid path")
    dst_dir = safe_target_dir(to_folder)
    moved = []

    src_txt = src_dir / f"{name}.txt"
    if src_txt.exists():
        dst_txt = dst_dir / f"{to_name}.txt"
        if dst_txt.exists():
            raise AppError("cílový přepis už existuje")
        src_txt.rename(dst_txt)
        moved.append("txt")

    if wav_path:
        src_wav = resolve_in_data(wav_path)
        if src_wav and src_wav.exists():
            dst_wav = dst_dir / AUDIO_SUBDIR / f"{to_name}.wav"
            dst_wav.parent.mkdir(parents=True, exist_ok=True)
            if dst_wav.exists():
                raise AppError("cílové audio už existuje")
            src_wav.rename(dst_wav)
            moved.append("wav")

    if not moved:
        raise AppError("položka nenalezena")
    return moved

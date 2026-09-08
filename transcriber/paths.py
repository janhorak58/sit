"""Path sanitization: everything the browser sends stays inside DATA_DIR."""

import re
from pathlib import Path

from .config import DATA_DIR

_ILLEGAL = re.compile(r'[\\/:*?"<>|]')


def sanitize_component(name, fallback):
    """Strip separators/reserved characters from a single file or folder name."""
    name = _ILLEGAL.sub("_", (name or "").strip())
    return name or fallback

def valid_component(name):
    """Return ``name`` only when it is a non-empty portable file component."""
    return bool(name and name not in {".", ".."} and sanitize_component(name, "") == name)


def safe_target_dir(folder):
    """Resolve ``folder`` under DATA_DIR, creating it. Falls back to DATA_DIR."""
    parts = [p for p in (folder or "").strip("/").split("/") if p not in ("", ".", "..")]
    root = DATA_DIR.resolve()
    d = (root.joinpath(*parts) if parts else root).resolve()
    if not d.is_relative_to(root):
        d = root
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_in_data(path):
    """Resolve a relative path under DATA_DIR, or None if it escapes / is empty."""
    if not path:
        return None
    target = (DATA_DIR / path).resolve()
    if not target.is_relative_to(DATA_DIR.resolve()):
        return None
    return target


def rel_to_data(path):
    """Path relative to DATA_DIR, as a string, for API responses."""
    return str(Path(path).relative_to(DATA_DIR))

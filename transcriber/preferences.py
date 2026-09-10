"""Validated, user-owned application preferences."""

import json
from copy import deepcopy

from .config import DATA_DIR

PREFERENCES_PATH = DATA_DIR / ".preferences.json"

DEFAULTS = {
    "profile": "meeting",
    "recording": {
        "default_project": "", "language": "cs", "microphone": "",
        "live_enabled": False, "live_chunk_seconds": "10",
        "speaker_count": "", "auto_diarize": True,
    },
    "transcript": {
        "show_timestamps": True, "show_speakers": True,
        "paragraph_size": "normal", "default_view": "both",
    },
    "brief": {
        "detail": "standard", "language": "same", "compare_previous": True,
        "user_prompt": "",
        "sections": {key: True for key in ("chapters", "decisions", "action_items", "open_questions", "risks", "speaker_contributions", "follow_up")},
    },
    "privacy": {"processing": "auto", "clear_live_drafts": True},
    "export": {"format": "markdown", "include_audio_links": False},
}


def _merge(defaults, values):
    merged = deepcopy(defaults)
    if not isinstance(values, dict):
        return merged
    for key, value in values.items():
        if key not in merged:
            continue
        if isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge(merged[key], value)
        elif isinstance(value, type(merged[key])) or merged[key] == "":
            merged[key] = value
    return merged


def get_preferences():
    try:
        return _merge(DEFAULTS, json.loads(PREFERENCES_PATH.read_text()))
    except (OSError, json.JSONDecodeError):
        return deepcopy(DEFAULTS)


def save_preferences(values):
    preferences = _merge(DEFAULTS, values)
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFERENCES_PATH.write_text(json.dumps(preferences, ensure_ascii=False, indent=2))
    return preferences


def merge_preferences(values):
    """Normalize request-local preferences without persisting them."""
    return _merge(DEFAULTS, values)

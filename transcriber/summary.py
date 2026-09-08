"""Structured meeting intelligence through the host-local omniroute.

omniroute is private infrastructure: this endpoint is called only by the
backend. The browser never receives its URL or direct access to it.

``summarize()`` returns a dict — never raw Markdown — so decisions, action
items and open questions stay individually addressable and, when transcript
segments are available, traceable back to a timestamp. ``render_markdown()``
renders that dict into the Markdown contract the library/download surface
already expects.
"""

import json

import httpx

from .config import GPT_OSS_API_KEY, OMNIROUTE_MODEL, OMNIROUTE_URL
from .errors import AppError

SYSTEM_PROMPT = """You extract structured meeting intelligence from a transcript.
Respond with ONLY a single JSON object — no prose, no Markdown, no code fences.
Write every text value in the transcript's own language. Never invent details
that are not in the transcript; omit a category by returning an empty array.

JSON shape:
{
  "summary": "1-3 factual sentences describing what the meeting covered",
  "decisions": [{"text": "...", "evidence_index": <int or null>}],
  "action_items": [{"text": "...", "owner": "name or null", "deadline": "date/phrase or null", "evidence_index": <int or null>}],
  "open_questions": [{"text": "...", "evidence_index": <int or null>}]
}

When the transcript is given as numbered timestamped lines, set
evidence_index to the line number that most directly supports the claim.
Otherwise always use null for evidence_index."""

EMPTY_SUMMARY = {"summary": "", "decisions": [], "action_items": [], "open_questions": []}

SUMMARY_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "meeting_summary",
        "schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "decisions": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}, "evidence_index": {"type": ["integer", "null"]}},
                    "required": ["text", "evidence_index"],
                    "additionalProperties": False,
                }},
                "action_items": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "owner": {"type": ["string", "null"]},
                        "deadline": {"type": ["string", "null"]},
                        "evidence_index": {"type": ["integer", "null"]},
                    },
                    "required": ["text", "owner", "deadline", "evidence_index"],
                    "additionalProperties": False,
                }},
                "open_questions": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}, "evidence_index": {"type": ["integer", "null"]}},
                    "required": ["text", "evidence_index"],
                    "additionalProperties": False,
                }},
            },
            "required": ["summary", "decisions", "action_items", "open_questions"],
            "additionalProperties": False,
        },
    },
}


def _transcript_lines(text, segments):
    """Numbered, timestamped transcript for evidence citation, when segments exist."""
    if not segments:
        return text
    lines = []
    for i, seg in enumerate(segments):
        start = seg.get("start", 0) or 0
        ts = f"{int(start) // 60:02d}:{int(start) % 60:02d}"
        speaker = f"{seg['speaker']}: " if seg.get("speaker") else ""
        lines.append(f"[{i}] {ts} {speaker}{(seg.get('text') or '').strip()}")
    return "\n".join(lines)


def _extract_json(content):
    """Best-effort JSON extraction: strip code fences, else find the first {...} block."""
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        content = content.split("\n", 1)[-1] if "\n" in content else content
        content = content.removeprefix("json").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def _resolve_evidence(index, segments):
    if not isinstance(index, int) or not segments or not (0 <= index < len(segments)):
        return None
    return segments[index].get("start")


def _normalize(parsed, segments):
    """Coerce a possibly-malformed model response into the guaranteed output shape."""
    def entry(item, extra_keys=()):
        if not isinstance(item, dict) or not item.get("text"):
            return None
        out = {
            "text": str(item["text"]).strip(),
            "segment_start": _resolve_evidence(item.get("evidence_index"), segments),
        }
        for key in extra_keys:
            value = item.get(key)
            out[key] = value.strip() if isinstance(value, str) and value.strip() else None
        return out

    def entry_list(key, extra_keys=()):
        raw = parsed.get(key)
        if not isinstance(raw, list):
            return []
        return [e for item in raw if (e := entry(item, extra_keys)) is not None]

    return {
        "summary": str(parsed.get("summary") or "").strip(),
        "decisions": entry_list("decisions"),
        "action_items": entry_list("action_items", extra_keys=("owner", "deadline")),
        "open_questions": entry_list("open_questions"),
    }


def summarize(text, project="", segments=None):
    """Ask the private LLM for structured meeting intelligence.

    Returns ``{summary, decisions[], action_items[], open_questions[]}``; each
    entry may carry ``segment_start`` (seconds) when it traces to transcript
    evidence, else ``None``. On unparseable model output, falls back to a
    dict carrying the raw response as ``summary`` rather than raising.
    """
    if not OMNIROUTE_URL:
        raise AppError("Souhrny nejsou nakonfigurované.")
    context = f"Project: {project}\n\n" if project else ""
    transcript = _transcript_lines(text, segments)
    headers = {"Authorization": f"Bearer {GPT_OSS_API_KEY}"} if GPT_OSS_API_KEY else None
    try:
        response = httpx.post(
            f"{OMNIROUTE_URL.rstrip('/')}/v1/chat/completions",
            json={
                "model": OMNIROUTE_MODEL,
                "stream": False,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": context + "Transcript:\n" + transcript},
                ],
                "response_format": SUMMARY_RESPONSE_FORMAT,
            },
            headers=headers,
            timeout=300.0,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise AppError(f"Souhrn se nepodařilo vytvořit: {exc}") from exc

    parsed = _extract_json(content)
    if not isinstance(parsed, dict):
        return {**EMPTY_SUMMARY, "summary": content.strip()}
    return _normalize(parsed, segments)


def render_markdown(data):
    """Render a ``summarize()`` result into the existing Markdown contract."""
    lines = []
    if data.get("summary"):
        lines += ["## Summary", data["summary"], ""]
    if data.get("decisions"):
        lines += ["## Decisions", *(f"- {d['text']}" for d in data["decisions"]), ""]
    if data.get("action_items"):
        lines.append("## Action items")
        for item in data["action_items"]:
            owner = f" ({item['owner']})" if item.get("owner") else ""
            deadline = f" — {item['deadline']}" if item.get("deadline") else ""
            lines.append(f"- {item['text']}{owner}{deadline}")
        lines.append("")
    if data.get("open_questions"):
        lines += ["## Open questions", *(f"- {q['text']}" for q in data["open_questions"]), ""]
    return "\n".join(lines).strip()

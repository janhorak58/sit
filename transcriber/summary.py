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
import logging

import httpx

from .config import GPT_OSS_API_KEY
from .connections import get_connection
from .errors import AppError

logger = logging.getLogger(__name__)


def remote_status():
    """Public reachability of the private LLM used for AI analysis."""
    connection = get_connection("llm")
    endpoint = connection["endpoint"]
    if not endpoint:
        return {"available": False, "url": None, "model": connection["model"], "last_error": "LLM endpoint is not set."}
    headers = {"Authorization": f"Bearer {GPT_OSS_API_KEY}"} if GPT_OSS_API_KEY else None
    try:
        response = httpx.get(f"{endpoint.rstrip('/')}/v1/models", headers=headers, timeout=2.0)
        # A model-list endpoint may not exist behind every gateway; anything
        # short of a server error still proves the port is up and routing.
        return {"available": response.status_code < 500, "url": endpoint, "model": connection["model"], "last_error": None}
    except httpx.HTTPError as exc:
        return {"available": False, "url": endpoint, "model": connection["model"], "last_error": str(exc)[:1000]}


SYSTEM_PROMPT = """You extract evidence-grounded meeting intelligence from a transcript.
Respond with ONLY a single JSON object — no prose, no Markdown, no code fences.
Write every text value in the transcript's own language. Never invent details:
when a field is unknown use null, and when a category has no evidence use [].
An evidence index must cite the numbered transcript line that supports the claim.

JSON shape:
{
  "summary": "2-4 factual sentences describing the outcome",
  "chapters": [{"title": "...", "summary": "...", "start_index": 0, "end_index": 3}],
  "decisions": [{"text": "...", "rationale": "..." or null, "alternatives": ["..."], "evidence_indexes": [0]}],
  "action_items": [{"text": "...", "owner": "name" or null, "deadline": "date/phrase" or null, "priority": "high|medium|low" or null, "evidence_indexes": [0]}],
  "open_questions": [{"text": "...", "owner": "name" or null, "deadline": "date/phrase" or null, "evidence_indexes": [0]}],
  "risks": [{"text": "...", "mitigation": "..." or null, "evidence_indexes": [0]}],
  "speaker_contributions": [{"speaker": "...", "commitments": ["..."], "decisions_proposed": ["..."], "unanswered_asks": ["..."]}],
  "follow_up": "A concise ready-to-send follow-up draft, or an empty string",
  "changes_since_last": ["..."]
}
For changes_since_last, compare only with the supplied previous-meeting context.
"""

EMPTY_SUMMARY = {
    "summary": "", "chapters": [], "decisions": [], "action_items": [],
    "open_questions": [], "risks": [], "speaker_contributions": [],
    "follow_up": "", "changes_since_last": [],
}

SUMMARY_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "meeting_intelligence",
        "schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "chapters": {"type": "array", "items": {"type": "object", "properties": {
                    "title": {"type": "string"}, "summary": {"type": "string"},
                    "start_index": {"type": "integer"}, "end_index": {"type": "integer"},
                }, "required": ["title", "summary", "start_index", "end_index"], "additionalProperties": False}},
                "decisions": {"type": "array", "items": {"type": "object", "properties": {
                    "text": {"type": "string"}, "rationale": {"type": ["string", "null"]},
                    "alternatives": {"type": "array", "items": {"type": "string"}},
                    "evidence_indexes": {"type": "array", "items": {"type": "integer"}},
                }, "required": ["text", "rationale", "alternatives", "evidence_indexes"], "additionalProperties": False}},
                "action_items": {"type": "array", "items": {"type": "object", "properties": {
                    "text": {"type": "string"}, "owner": {"type": ["string", "null"]},
                    "deadline": {"type": ["string", "null"]}, "priority": {"type": ["string", "null"]},
                    "evidence_indexes": {"type": "array", "items": {"type": "integer"}},
                }, "required": ["text", "owner", "deadline", "priority", "evidence_indexes"], "additionalProperties": False}},
                "open_questions": {"type": "array", "items": {"type": "object", "properties": {
                    "text": {"type": "string"}, "owner": {"type": ["string", "null"]},
                    "deadline": {"type": ["string", "null"]}, "evidence_indexes": {"type": "array", "items": {"type": "integer"}},
                }, "required": ["text", "owner", "deadline", "evidence_indexes"], "additionalProperties": False}},
                "risks": {"type": "array", "items": {"type": "object", "properties": {
                    "text": {"type": "string"}, "mitigation": {"type": ["string", "null"]},
                    "evidence_indexes": {"type": "array", "items": {"type": "integer"}},
                }, "required": ["text", "mitigation", "evidence_indexes"], "additionalProperties": False}},
                "speaker_contributions": {"type": "array", "items": {"type": "object", "properties": {
                    "speaker": {"type": "string"}, "commitments": {"type": "array", "items": {"type": "string"}},
                    "decisions_proposed": {"type": "array", "items": {"type": "string"}},
                    "unanswered_asks": {"type": "array", "items": {"type": "string"}},
                }, "required": ["speaker", "commitments", "decisions_proposed", "unanswered_asks"], "additionalProperties": False}},
                "follow_up": {"type": "string"},
                "changes_since_last": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "chapters", "decisions", "action_items", "open_questions", "risks", "speaker_contributions", "follow_up", "changes_since_last"],
            "additionalProperties": False,
        },
    },
}


def _transcript_lines(text, segments):
    """Create compact speaker turns while retaining their raw evidence bounds."""
    if not segments:
        return text, []
    turns = []
    for index, seg in enumerate(segments):
        value = (seg.get("text") or "").strip()
        if not value:
            continue
        start, end = float(seg.get("start", 0) or 0), float(seg.get("end", 0) or 0)
        speaker = seg.get("speaker")
        previous = turns[-1] if turns else None
        if previous and previous["speaker"] == speaker and start - previous["end"] <= 2.5 and len(previous["text"]) + len(value) < 700:
            previous["text"] += " " + value
            previous["end"], previous["source_indexes"] = end, previous["source_indexes"] + [index]
        else:
            turns.append({"start": start, "end": end, "speaker": speaker, "text": value, "source_indexes": [index]})
    lines = [
        f"[{i}] {int(turn['start']) // 60:02d}:{int(turn['start']) % 60:02d} "
        f"{turn['speaker'] + ': ' if turn['speaker'] else ''}{turn['text']}"
        for i, turn in enumerate(turns)
    ]
    return "\n".join(lines), turns


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


def _evidence(indexes, turns):
    if not isinstance(indexes, list):
        indexes = [indexes] if isinstance(indexes, int) else []
    return [
        {"start": turn["start"], "end": turn["end"], "speaker": turn["speaker"]}
        for index in indexes
        if isinstance(index, int) and 0 <= index < len(turns)
        for turn in [turns[index]]
    ]


def _clean(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalize(parsed, turns):
    """Coerce a possibly-malformed model response into the guaranteed output shape."""
    def entry(item, extra_keys=()):
        if not isinstance(item, dict) or not _clean(item.get("text")):
            return None
        indexes = item.get("evidence_indexes", item.get("evidence_index"))
        out = {"text": _clean(item["text"]), "evidence": _evidence(indexes, turns)}
        for key in extra_keys:
            value = item.get(key)
            out[key] = [_clean(v) for v in value if _clean(v)] if key == "alternatives" and isinstance(value, list) else _clean(value)
        return out

    def entry_list(key, extra_keys=()):
        raw = parsed.get(key)
        return [item for value in raw if (item := entry(value, extra_keys))] if isinstance(raw, list) else []

    chapters = []
    for item in parsed.get("chapters", []):
        if not isinstance(item, dict) or not _clean(item.get("title")):
            continue
        start, end = item.get("start_index"), item.get("end_index")
        chapters.append({
            "title": _clean(item["title"]), "summary": _clean(item.get("summary")) or "",
            "evidence": _evidence(list(range(start, end + 1)) if isinstance(start, int) and isinstance(end, int) and end >= start else [], turns),
        })
    contributions = []
    for item in parsed.get("speaker_contributions", []):
        if not isinstance(item, dict) or not _clean(item.get("speaker")):
            continue
        contributions.append({"speaker": _clean(item["speaker"]), **{
            key: [_clean(value) for value in item.get(key, []) if _clean(value)]
            for key in ("commitments", "decisions_proposed", "unanswered_asks")
        }})
    return {
        "summary": _clean(parsed.get("summary")) or "", "chapters": chapters,
        "decisions": entry_list("decisions", ("rationale", "alternatives")),
        "action_items": entry_list("action_items", ("owner", "deadline", "priority")),
        "open_questions": entry_list("open_questions", ("owner", "deadline")),
        "risks": entry_list("risks", ("mitigation",)),
        "speaker_contributions": contributions,
        "follow_up": _clean(parsed.get("follow_up")) or "",
        "changes_since_last": [_clean(value) for value in parsed.get("changes_since_last", []) if _clean(value)],
    }


def summarize(text, project="", segments=None, previous_context="", instructions=""):
    """Ask the private LLM for evidence-grounded meeting intelligence."""
    connection = get_connection("llm")
    endpoint = connection["endpoint"]
    if not endpoint:
        raise AppError("Summaries are not configured.")
    transcript, turns = _transcript_lines(text, segments)
    context = f"Project: {project}\n\n" if project else ""
    if instructions:
        context += f"Brief preferences: {instructions}\n\n"
    if previous_context:
        context += f"Previous meeting context (for comparison only):\n{previous_context}\n\n"
    headers = {"Authorization": f"Bearer {GPT_OSS_API_KEY}"} if GPT_OSS_API_KEY else None
    try:
        response = httpx.post(
            f"{endpoint.rstrip('/')}/v1/chat/completions",
            json={
                "model": connection["model"], "stream": False,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": context + "Transcript:\n" + transcript},
                ],
                "response_format": SUMMARY_RESPONSE_FORMAT,
            },
            headers=headers, timeout=300.0,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except httpx.RequestError as exc:
        logger.warning("AI analysis server unreachable at %s: %s", endpoint, exc)
        raise AppError(
            "The AI analysis server could not be reached. Check the Connections panel."
        ) from exc
    except (httpx.HTTPStatusError, KeyError, ValueError) as exc:
        logger.warning("AI analysis failed: %s", exc)
        raise AppError(
            "The AI analysis could not be generated. Check the Connections panel."
        ) from exc
    parsed = _extract_json(content)
    return {**EMPTY_SUMMARY, "summary": content.strip()} if not isinstance(parsed, dict) else _normalize(parsed, turns)


def render_markdown(data):
    """Render the portable Markdown representation of the intelligence brief."""
    lines = ["## Summary", data["summary"], ""] if data.get("summary") else []
    sections = (
        ("Chapters", [f"- {item['title']}: {item['summary']}" for item in data.get("chapters", [])]),
        ("Decisions", [f"- {item['text']}" for item in data.get("decisions", [])]),
        ("Action items", [f"- {item['text']}" + (f" ({item['owner']})" if item.get("owner") else "") + (f" — {item['deadline']}" if item.get("deadline") else "") for item in data.get("action_items", [])]),
        ("Open questions", [f"- {item['text']}" for item in data.get("open_questions", [])]),
        ("Risks", [f"- {item['text']}" for item in data.get("risks", [])]),
        ("Changes since last meeting", [f"- {item}" for item in data.get("changes_since_last", [])]),
    )
    for title, items in sections:
        if items:
            lines += [f"## {title}", *items, ""]
    if data.get("follow_up"):
        lines += ["## Follow-up draft", data["follow_up"], ""]
    return "\n".join(lines).strip()

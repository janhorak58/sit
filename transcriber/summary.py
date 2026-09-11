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


def _auth_headers(connection):
    """Bearer header from the saved API key, falling back to the env var."""
    key = connection.get("api_key") or GPT_OSS_API_KEY
    return {"Authorization": f"Bearer {key}"} if key else None


def remote_status():
    """Public reachability of the private LLM used for AI analysis."""
    connection = get_connection("llm")
    endpoint = connection["endpoint"]
    if not endpoint:
        return {"available": False, "url": None, "model": connection["model"], "last_error": "LLM endpoint is not set.", "api_key": False}
    report = {
        "url": endpoint,
        "model": connection["model"],
        "api_key": bool(connection.get("api_key") or GPT_OSS_API_KEY),
    }
    try:
        response = httpx.get(
            f"{endpoint.rstrip('/')}/v1/models", headers=_auth_headers(connection), timeout=2.0
        )
        # A rejected key looks like a healthy port, and reporting that as
        # "running" is what made an unauthorized analysis unexplainable.
        if response.status_code in (401, 403):
            return {
                **report,
                "available": False,
                "last_error": (
                    f"The LLM server rejected the API key (HTTP {response.status_code}). "
                    "Set it in Connections."
                ),
            }
        # A model-list endpoint may not exist behind every gateway; anything
        # short of a server error still proves the port is up and routing.
        return {**report, "available": response.status_code < 500, "last_error": None}
    except httpx.HTTPError as exc:
        return {**report, "available": False, "last_error": str(exc)[:1000]}


SYSTEM_PROMPT = """You extract evidence-grounded meeting intelligence from a transcript.
Respond with ONLY a single JSON object — no prose, no Markdown, no code fences.
Write every text value in the transcript's own language. Never invent details:
when a field is unknown use null, and when a category has no evidence use [].

Input format: one numbered line per speaker turn, `[i] mm:ss SPEAKER: text`.
The text is machine-transcribed, so expect misheard words, missing diacritics
and occasional foreign-language fragments. Read through those errors for the
intent; never quote a garbled phrase as if it were exact. Use the speaker
labels exactly as given, and resolve "he/she/they/I" to a label whenever the
turn order makes the referent unambiguous.

Write for someone who did not attend and has two minutes. Every item must be
specific enough to act on without replaying the recording: name the system,
person, number, or document involved instead of "the topic" or "the issue".

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

Rules per field:
- summary: what was settled and what happens next, not what was discussed.
  Never open with "The participants discussed"; lead with the outcome.
- chapters: cover the meeting in order, without gaps or overlaps, one per
  topic shift (typically 3-8). Titles are noun phrases of at most six words.
  start_index/end_index are transcript line numbers.
- decisions: only settled choices. If a choice was later reversed, record the
  final one and note the reversal in the rationale. rationale is the reason
  actually voiced; alternatives are options that were considered and dropped.
- action_items: start with an imperative verb ("Send the draft to Jan").
  owner only when someone accepted the work or was assigned it by name — a
  first-person commitment makes that speaker the owner; otherwise null.
  deadline keeps the transcript's own wording ("by Friday", "before the
  release"); never convert it into a calendar date that was not said.
  priority is "high" only when the meeting called it urgent or blocking.
- open_questions: questions still unanswered when the recording ends, not
  questions that were answered later in the conversation.
- risks: a concrete threat plus who or what it hits; mitigation only if one
  was proposed. Do not restate an action item as a risk.
- speaker_contributions: one entry per speaker who committed to something,
  proposed a decision, or left an ask unanswered. Skip silent participants.
- follow_up: an e-mail body a participant could send unedited — decisions,
  then actions with owners and deadlines, then the next step. No greeting
  boilerplate, no invented recipients.
- changes_since_last: compare only with the supplied previous-meeting context;
  leave empty when no context is supplied.

Quality bar:
- Drop small talk, scheduling chatter, and technical audio problems unless
  they produced a decision or an action.
- Merge duplicates: one item per real commitment, even when it was repeated.
  Do not place the same content in two categories.
- Prefer fewer, sharper items over exhaustive coverage; an empty array is
  better than a filler entry.
- evidence_indexes cite the one to three lines that actually support the item;
  never cite a line that does not mention it.
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


def _failure_detail(exc):
    """What the LLM server actually said, for an error the user can act on.

    A private endpoint rejecting the request (unknown model, unsupported
    response_format, out of context) answers with a body that names the cause.
    Hiding it behind "check the Connections panel" left no way to tell those
    apart from a broken tunnel without reading the backend log.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return f"{type(exc).__name__}: {exc}"[:500]
    body = (response.text or "").strip().replace("\n", " ")
    return f"HTTP {response.status_code} from the LLM server: {body[:400] or '(empty body)'}"


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
    headers = _auth_headers(connection)
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
            "The AI analysis server could not be reached. Check the Connections panel. "
            f"Detail: {exc}"
        ) from exc
    except (httpx.HTTPStatusError, KeyError, ValueError) as exc:
        logger.warning("AI analysis failed: %s", exc)
        raise AppError(
            "The AI analysis could not be generated. Check the Connections panel. "
            f"Detail: {_failure_detail(exc)}"
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

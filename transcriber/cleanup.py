"""AI correction of speech-recognition errors, cue by cue.

The model may fix misheard words, names, glossary terms, punctuation and
capitalization. It never sees or returns timestamps or speakers, so playback
and analysis evidence stay aligned. Each answer is compared with the cue it
replaces: an answer that rewrites the sentence instead of correcting it is
dropped, and the recognized text is kept.
"""

import difflib
import logging
import re

import httpx

from .connections import get_connection
from .errors import AppError
from .summary import _auth_headers, _extract_json, _failure_detail

logger = logging.getLogger(__name__)

# One request covers this much cue text. Small enough for a fast answer and a
# short context, large enough that a long meeting needs only a few dozen calls.
BATCH_CHARS = 4000
# Room for every cue of a batch to come back corrected. A model stuck repeating
# itself stops here instead of running into the request timeout.
MAX_ANSWER_TOKENS = 4096
# Corrected cues that precede a batch, shown so its first sentence has context.
CONTEXT_CUES = 3
# Character similarity below this means the model rewrote the cue.
MIN_SIMILARITY = 0.5
ECHOED_ID = re.compile(r"^\[\d+\]\s*")


SYSTEM_PROMPT = """You correct speech-recognition errors in a meeting transcript.
Each line is one cue: [id] speaker: text. Fix only what the recognizer clearly got wrong:
misspelled or misheard words, names and technical terms, wrong word boundaries,
punctuation and capitalization. Change a word only when the surrounding text makes the
intended word certain. Never guess what a garbled fragment meant, never complete an
unfinished sentence and never add or drop words, including filler words and repetitions.
The glossary gives correct spellings. Use it to repair a garbled or misspelled form of a
listed term, keeping the grammatical case. Never swap in a glossary term for a word the
speaker really said, such as a nickname or a different word for the same thing.
Keep the speaker's wording, word order, language, dialect and register. Never translate,
summarize, merge or split cues.
Lines under "Context" only help you understand the first cues. Never return them.
Respond with ONLY a JSON object {"cues": [{"id": <id>, "text": "<corrected cue>"}]}
that lists only the cues you changed. Return {"cues": []} when nothing needs fixing."""

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "transcript_corrections",
        "schema": {
            "type": "object",
            "properties": {
                "cues": {"type": "array", "items": {"type": "object", "properties": {
                    "id": {"type": "integer"}, "text": {"type": "string"},
                }, "required": ["id", "text"], "additionalProperties": False}},
            },
            "required": ["cues"],
            "additionalProperties": False,
        },
    },
}


def _batches(texts):
    """Indexes of non-empty cues, grouped into requests of about BATCH_CHARS."""
    batch, size = [], 0
    for index, text in enumerate(texts):
        if not text:
            continue
        if batch and size + len(text) > BATCH_CHARS:
            yield batch
            batch, size = [], 0
        batch.append(index)
        size += len(text)
    if batch:
        yield batch


def _accepted(original, corrected, speaker):
    """The corrected cue, or None when it is unchanged or a rewrite."""
    corrected = corrected.strip() if isinstance(corrected, str) else ""
    # The model sometimes copies the line's "[id] speaker:" prefix into the text.
    corrected = ECHOED_ID.sub("", corrected)
    if speaker and corrected.startswith(f"{speaker}:"):
        corrected = corrected[len(speaker) + 1:].strip()
    if not corrected or corrected == original:
        return None
    similarity = difflib.SequenceMatcher(None, original.lower(), corrected.lower()).ratio()
    return corrected if similarity >= MIN_SIMILARITY else None


def _ask(connection, message):
    endpoint = connection["endpoint"]
    try:
        response = httpx.post(
            f"{endpoint.rstrip('/')}/v1/chat/completions",
            json={
                "model": connection["model"], "stream": False,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": message},
                ],
                "response_format": RESPONSE_FORMAT,
                "max_tokens": MAX_ANSWER_TOKENS,
                "temperature": 0,
            },
            headers=_auth_headers(connection), timeout=300.0,
        )
        response.raise_for_status()
        return _extract_json(response.json()["choices"][0]["message"]["content"])
    except httpx.RequestError as exc:
        raise AppError(f"The AI server could not be reached. Check the Connections panel. Detail: {exc}") from exc
    except (httpx.HTTPStatusError, KeyError, ValueError) as exc:
        raise AppError(f"The AI correction failed. Detail: {_failure_detail(exc)}") from exc


def correct_cues(cues, glossary="", project="", language=None, on_batch=None):
    """Ask the LLM to fix recognition errors in segment dicts.

    Returns ``({cue index: corrected text}, model)`` for the cues it changed.
    ``on_batch(done, total)`` reports progress before each request.
    """
    connection = get_connection("llm")
    if not connection["endpoint"]:
        raise AppError("AI correction needs the AI server. Set it in Connections.")
    texts = [(cue.get("text") or "").strip() for cue in cues]
    fixes = {}

    def line(index):
        speaker = cues[index].get("speaker")
        return f"[{index}] {speaker + ': ' if speaker else ''}{fixes.get(index, texts[index])}"

    header = "".join([
        f"Project: {project}\n" if project else "",
        f"Language: {language}\n" if language else "",
        f"Glossary (correct spelling of names and terms):\n{glossary.strip()}\n" if glossary.strip() else "",
    ])
    batches = list(_batches(texts))
    for done, batch in enumerate(batches):
        if on_batch:
            on_batch(done, len(batches))
        context = [index for index in range(max(0, batch[0] - CONTEXT_CUES), batch[0]) if texts[index]]
        message = header + "\n"
        if context:
            message += "Context:\n" + "\n".join(map(line, context)) + "\n\n"
        message += "Cues:\n" + "\n".join(map(line, batch))
        parsed = _ask(connection, message)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("cues"), list):
            logger.warning("AI correction returned no usable JSON for cues %d-%d", batch[0], batch[-1])
            continue
        allowed = set(batch)
        for item in parsed["cues"]:
            index = item.get("id") if isinstance(item, dict) else None
            if index in allowed and (text := _accepted(texts[index], item.get("text"), cues[index].get("speaker"))):
                fixes[index] = text
    return fixes, connection["model"]

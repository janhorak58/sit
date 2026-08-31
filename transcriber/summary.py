"""Meeting summaries through the host-local, OpenAI-compatible omniroute.

omniroute is private infrastructure: this endpoint is called only by the
backend. The browser never receives its URL or direct access to it.
"""

import httpx

from .config import OMNIROUTE_MODEL, OMNIROUTE_URL
from .errors import AppError

SYSTEM_PROMPT = """You summarize meeting transcripts. Write in the transcript's
language. Be factual; never invent missing details. Use Markdown with these
sections: Summary, Decisions, Action items, Open questions. Omit empty sections.
For each action item include owner and deadline only when explicitly stated."""


def summarize(text, project=""):
    if not OMNIROUTE_URL:
        raise AppError("Souhrny nejsou nakonfigurované.")
    context = f"Project: {project}\n\n" if project else ""
    try:
        response = httpx.post(
            f"{OMNIROUTE_URL.rstrip('/')}/v1/chat/completions",
            json={
                "model": OMNIROUTE_MODEL,
                "stream": False,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": context + "Transcript:\n" + text},
                ],
            },
            timeout=300.0,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise AppError(f"Souhrn se nepodařilo vytvořit: {exc}") from exc

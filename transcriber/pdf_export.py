"""Render a stored AI analysis as a PDF meeting brief.

Mirrors the sections of the meeting view. DejaVu Sans is bundled because the
PDF core fonts are Latin-1 only and cannot draw Czech diacritics.
"""

from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

FONT_DIR = Path(__file__).parent / "fonts"
FONT = "DejaVu"
INK = (23, 32, 30)
MUTED = (98, 110, 106)
ACCENT = (217, 3, 104)
RULE = (226, 222, 216)
# Sections whose entries are free text, not a headline with details.
PROSE_SECTIONS = {"Follow-up draft", "What changed"}


def _time(seconds):
    total = max(0, int(seconds or 0))
    return f"{total // 60:02d}:{total % 60:02d}"


def _starts(evidence):
    return sorted({source["start"] for source in evidence or [] if isinstance(source, dict) and source.get("start") is not None})


def _sources(evidence):
    starts = _starts(evidence)
    return "Sources: " + ", ".join(_time(start) for start in starts) if starts else None


def _span(evidence):
    starts = _starts(evidence)
    if not starts:
        return None
    return _time(starts[0]) if len(starts) == 1 else f"{_time(starts[0])}–{_time(starts[-1])}"


def _joined(label, values):
    return f"{label}: {' · '.join(values)}" if values else None


def _sections(summary):
    """(title, [(text, [detail, ...])]) in the order the meeting view shows them."""
    return [
        ("Timeline", [
            (item.get("title", ""), [item.get("summary"), _span(item.get("evidence"))])
            for item in summary.get("chapters") or []
        ]),
        ("Decisions", [
            (item.get("text", ""), [
                item.get("rationale") and f"Why: {item['rationale']}",
                _joined("Alternatives", item.get("alternatives")),
                _sources(item.get("evidence")),
            ])
            for item in summary.get("decisions") or []
        ]),
        ("Action items", [
            (item.get("text", ""), [
                " · ".join(filter(None, [item.get("owner"), item.get("deadline"), item.get("priority") and f"{item['priority']} priority"])),
                _sources(item.get("evidence")),
            ])
            for item in summary.get("action_items") or []
        ]),
        ("Open questions", [
            (item.get("text", ""), [
                " · ".join(filter(None, [item.get("owner") and f"Owner: {item['owner']}", item.get("deadline") and f"By {item['deadline']}"])),
                _sources(item.get("evidence")),
            ])
            for item in summary.get("open_questions") or []
        ]),
        ("Risks", [
            (item.get("text", ""), [item.get("mitigation") and f"Mitigation: {item['mitigation']}", _sources(item.get("evidence"))])
            for item in summary.get("risks") or []
        ]),
        ("Speaker contributions", [
            (item.get("speaker", ""), [
                _joined("Commitments", item.get("commitments")),
                _joined("Decisions proposed", item.get("decisions_proposed")),
                _joined("Unanswered asks", item.get("unanswered_asks")),
            ])
            for item in summary.get("speaker_contributions") or []
        ]),
        ("Follow-up draft", [(summary["follow_up"], [])] if summary.get("follow_up") else []),
        ("What changed", [(text, []) for text in summary.get("changes_since_last") or []]),
    ]


class _Brief(FPDF):
    def footer(self):
        self.set_y(-14)
        self.set_font(FONT, "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, f"Page {self.page_no()}/{{nb}}", align="R")


def _paragraph(pdf, text, size, style="", color=INK, height=None, indent=0):
    pdf.set_font(FONT, style, size)
    pdf.set_text_color(*color)
    pdf.set_x(pdf.l_margin + indent)
    pdf.multi_cell(0, height or size * 0.5, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _heading(pdf, title, count=None):
    pdf.ln(5)
    _paragraph(pdf, title.upper() + (f"  ({count})" if count is not None else ""), 10, "B", ACCENT)
    pdf.set_draw_color(*RULE)
    pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
    pdf.ln(3)


def _markdown(pdf, markdown):
    """Older analyses were stored as Markdown only."""
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            pdf.ln(2)
        elif stripped.startswith("#"):
            _heading(pdf, stripped.lstrip("#").strip())
        elif stripped.startswith(("- ", "* ")):
            _paragraph(pdf, "• " + stripped[2:], 10.5, indent=2)
        else:
            _paragraph(pdf, stripped, 10.5)


def render_summary_pdf(summary, title, stale=False):
    """PDF bytes for a stored analysis (structured or legacy Markdown)."""
    pdf = _Brief(format="A4")
    pdf.set_title(f"{title} — AI analysis")
    pdf.set_creator("SIT")
    pdf.add_font(FONT, "", str(FONT_DIR / "DejaVuSans.ttf"))
    pdf.add_font(FONT, "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, margin=20)
    pdf.add_page()

    _paragraph(pdf, "MEETING BRIEF", 9, "B", ACCENT)
    pdf.ln(1)
    _paragraph(pdf, title, 20, "B", height=9)
    if stale:
        pdf.ln(2)
        _paragraph(pdf, "This analysis is outdated: the transcript was edited after it was generated.", 9, color=MUTED)

    if summary.get("markdown"):
        pdf.ln(2)
        _markdown(pdf, summary["markdown"])
        return bytes(pdf.output())

    if summary.get("summary"):
        pdf.ln(4)
        _paragraph(pdf, summary["summary"], 11.5, height=6)
    for section_title, items in _sections(summary):
        if not items:
            continue
        _heading(pdf, section_title, len(items))
        for text, details in items:
            _paragraph(pdf, text, 10.5, "" if section_title in PROSE_SECTIONS else "B", height=5.5)
            for detail in filter(None, details):
                _paragraph(pdf, detail, 9.5, color=MUTED, height=5)
            pdf.ln(3)
    return bytes(pdf.output())

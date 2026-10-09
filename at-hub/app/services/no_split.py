"""No word on a document's line table is ever split across two lines (an item # like 79299-HPC-NUT, a header like
BACKORDERED). Every column is at least as wide as its widest single word; the description column gives up the room
and wraps at spaces. Used by both table renderers -- the built-in layouts (pdf._data_table) and the Template
Designer's table block -- so it holds for every template, now and later.

    fit_columns(...) -> (widths, fits): widths with no word split; fits=False when even then the page is too narrow
                                        (the caller sets the table a size smaller and tries again)
"""
import re
from typing import List, Optional, Sequence

from reportlab.pdfbase import pdfmetrics

TAG = re.compile(r"<[^>]+>")


def plain(text) -> str:
    """Paragraph markup -> the words it shows."""
    t = getattr(text, "text", text)
    t = TAG.sub(" ", str(t or "")).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
    return t


def widest_word(texts: Sequence, font: str, size: float) -> float:
    best = 0.0
    for t in texts:
        for w in plain(t).split():
            best = max(best, pdfmetrics.stringWidth(w, font, size))
    return best


def fit_columns(widths: List[float], cells: List[List], headers: List, flex: List[int], *, font: str, size: float,
                head_font: str, head_size: float, pad: float, bold_cols: Sequence[int] = (), bold_font: Optional[str] = None,
                flex_floor: float = 72.0) -> tuple:
    """widths: the design's column widths (points); cells[i]: column i's values; flex: the columns that may wrap
    (description) and give up room. Returns (new widths, everything fits)."""
    total = sum(widths)
    need = []
    for i, w in enumerate(widths):
        if i in flex:
            need.append(min(w, flex_floor))
            continue
        f = bold_font if (bold_font and i in bold_cols) else font
        m = max(widest_word(cells[i] if i < len(cells) else [], f, size), widest_word([headers[i]] if i < len(headers) else [], head_font, head_size))
        need.append(m + 2 * pad + 1.5)
    out = [w if i in flex else max(w, need[i]) for i, w in enumerate(widths)]
    over = sum(out) - total
    if over > 0.5 and flex:
        room = [(i, out[i] - need[i]) for i in flex if out[i] > need[i]]
        spare = sum(r for _, r in room)
        take = min(over, spare)
        for i, r in room:
            out[i] -= take * (r / spare) if spare else 0
        over -= take
    if over > 0.5:  # nothing flexible left: take it from columns wider than they need
        room = [(i, out[i] - need[i]) for i in range(len(out)) if out[i] > need[i] + 0.5]
        spare = sum(r for _, r in room)
        take = min(over, spare)
        for i, r in room:
            out[i] -= take * (r / spare) if spare else 0
        over -= take
    return out, over <= 0.5

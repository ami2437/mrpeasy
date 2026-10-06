"""Draws a designer template (spec) with a record's data into a PDF.

spec = {
  "page":   {"w": 8.5, "h": 11, "margin": 0.6},                         # inches
  "font":   "sans",
  "header": {"h": 2.4, "blocks": [...]},        # page 1 top (labels: the whole label)
  "running":{"h": 0.4, "blocks": [...]},        # top of pages 2+ (optional)
  "table":  {"columns": [{"key", "header", "w", "align"}], "style": {...}},   # documents only
  "pallets":{"heading": "Pallet Information", "new_page": true, "columns": [...], "style": {...}},
                                                # packing lists: a pallet table after the lines (only when it has pallets)
  "summary":{"h": 1.6, "blocks": [...]},        # right after the table(s), kept together
  "footer": {"h": 0.5, "blocks": [...]},        # every page bottom
}
block = {"type": text|image|barcode|qr|rect|line, "x","y","w","h" (inches from the band's top-left),
         "text": "Bill to\n{{customer.name}}", "value": "{{shipment.code}}", "style": {...}}
Text: {{field}} placeholders; a line whose fields are all empty is dropped ("Attn: {{customer.contact}}").
Show / hide (the designer's checklist): spec["hidden"] = section names left off ("Logo", or "Totals" for every
"Totals: ..." part), block["hidden"], block["hide_fields"] = fields whose lines / list rows are left off, and
column["hidden"] -- visible_spec() applies them before anything is drawn.
"""
import base64
import copy
import io
import re
from xml.sax.saxutils import escape

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import code128, qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.platypus.flowables import HRFlowable

from app.services.pdf import FONT as BASE_FONT, FONT_BOLD as BASE_BOLD, FONT_ITALIC as BASE_ITALIC

# Font families a template can choose (spec["font"]): "sans" = the built-in PDFs' font; "ui" = Segoe UI, as in the
# design samples (falls back to sans where it isn't installed, e.g. a Linux server without it).
FAMILIES = {"sans": (BASE_FONT, BASE_BOLD, BASE_ITALIC, BASE_BOLD)}


def _register_ui():
    import os
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    d = "C:/Windows/Fonts/"
    files = {"UI": "segoeui.ttf", "UI-Bold": "segoeuib.ttf", "UI-Italic": "segoeuii.ttf", "UI-Semi": "seguisb.ttf"}
    if not all(os.path.exists(d + f) for k, f in files.items() if k != "UI-Semi"):
        return
    for name, f in files.items():
        path = d + f if os.path.exists(d + f) else d + files["UI-Bold"]
        pdfmetrics.registerFont(TTFont(name, path))
    FAMILIES["ui"] = ("UI", "UI-Bold", "UI-Italic", "UI-Semi")


_register_ui()


def _register_gothic():
    """Century Gothic -- the old portal's packing list and invoice font (spec["font"] = "gothic"); sans where it isn't installed."""
    import os
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    d = "C:/Windows/Fonts/"
    files = {"Gothic": "GOTHIC.TTF", "Gothic-Bold": "GOTHICB.TTF", "Gothic-Italic": "GOTHICI.TTF"}
    if not all(os.path.exists(d + f) for f in files.values()):
        return
    for name, f in files.items():
        pdfmetrics.registerFont(TTFont(name, d + f))
    pdfmetrics.registerFontFamily("Gothic", normal="Gothic", bold="Gothic-Bold", italic="Gothic-Italic", boldItalic="Gothic-Bold")  # <b> in text
    FAMILIES["gothic"] = ("Gothic", "Gothic-Bold", "Gothic-Italic", "Gothic-Bold")


_register_gothic()
FONT, FONT_BOLD, FONT_ITALIC, FONT_SEMI = FAMILIES["sans"]


def use_family(name):
    global FONT, FONT_BOLD, FONT_ITALIC, FONT_SEMI
    FONT, FONT_BOLD, FONT_ITALIC, FONT_SEMI = FAMILIES.get(name or "sans", FAMILIES["sans"])


PH = re.compile(r"\{\{\s*([\w.]+)\s*(?:\|([^}]*))?\}\}")  # {{field}} or {{field|shown when empty}}
ALIGN = {"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT}


def color(v, default=None):
    if not v:
        return default
    try:
        return colors.HexColor(v) if str(v).startswith("#") else getattr(colors, v)
    except Exception:
        return default


def lookup(ctx, key):
    cur = ctx
    for part in key.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return ""
    return "" if cur is None else str(cur)


def fill(text, ctx, markup=True):
    """Fill {{fields}}; drop lines whose fields are all empty. markup=True keeps the template's own <b>/<i>
    tags and escapes only the filled-in values."""
    out = []
    for line in str(text or "").split("\n"):
        found = PH.findall(line)
        vals = [lookup(ctx, k) for k, _ in found]
        if found and not any(v.strip() for v in vals) and not any(d for _, d in found):
            continue
        vals = [v if v.strip() else d for v, (_, d) in zip(vals, found)]
        it = iter(vals)
        filled = PH.sub(lambda m: (escape(next(it)) if markup else next(it)), line)
        out.append(filled)
    return "\n".join(out)


def is_hidden(spec, b):
    hidden = set(spec.get("hidden") or [])
    g = b.get("group") or ""
    return bool(b.get("hidden")) or (g and (g in hidden or g.split(":")[0].strip() in hidden))


def drop_field_lines(text, keys):
    """Leave out every line (or list row) that shows one of these fields."""
    keys = set(keys or [])
    if not keys or not text:
        return text
    return "\n".join(l for l in str(text).split("\n") if not any(k in keys for k, _ in PH.findall(l)))


def visible_spec(spec):
    """The spec as it prints: hidden sections, blocks, fields and columns taken out."""
    out = copy.deepcopy(spec or {})
    for k in ("header", "running", "summary", "footer"):
        band = out.get(k)
        if not isinstance(band, dict):
            continue
        keep = []
        for b in band.get("blocks") or []:
            if is_hidden(out, b):
                continue
            if b.get("hide_fields"):
                for key in ("text", "value"):
                    if b.get(key):
                        b[key] = drop_field_lines(b[key], b["hide_fields"])
            keep.append(b)
        band["blocks"] = keep
    for k in ("table", "pallets"):
        if isinstance(out.get(k), dict):
            out[k]["columns"] = [c for c in out[k].get("columns") or [] if not c.get("hidden")]
    return out


# ---------- one block ----------
def _font(st):
    return FONT_BOLD if st.get("bold") else FONT_SEMI if st.get("semi") else FONT_ITALIC if st.get("italic") else FONT


def _para_style(st, size=None):
    return ParagraphStyle("b", fontName=_font(st),
                          fontSize=size or float(st.get("size", 9)), leading=(size or float(st.get("size", 9))) * float(st.get("lh", 1.25)),
                          textColor=color(st.get("color"), colors.HexColor("#1e293b")), alignment=ALIGN.get(st.get("align", "left"), TA_LEFT))


def draw_block(c, b, ctx, ox, oy):
    """ox, oy: the band's top-left corner on the page (points, y up)."""
    st = b.get("style") or {}
    x, y_top = ox + float(b.get("x", 0)) * inch, oy - float(b.get("y", 0)) * inch
    w, h = float(b.get("w", 1)) * inch, float(b.get("h", 0.3)) * inch
    y = y_top - h
    t = b.get("type", "text")
    # box: fill + border (every type can have one)
    bg, bc, bw, rad = color(st.get("bg")), color(st.get("border_color"), colors.HexColor("#cbd5e1")), float(st.get("border", 0) or 0), float(st.get("radius", 0) or 0)
    if t == "line":
        c.saveState()
        c.setStrokeColor(color(st.get("color"), colors.HexColor("#1e293b")))
        c.setLineWidth(float(st.get("border", 1) or 1))
        if w >= h:
            c.line(x, y + h / 2, x + w, y + h / 2)
        else:
            c.line(x + w / 2, y, x + w / 2, y + h)
        c.restoreState()
        return
    if bg or bw:
        c.saveState()
        if bg:
            c.setFillColor(bg)
        c.setStrokeColor(bc)
        c.setLineWidth(bw or 0.01)
        if rad:
            c.roundRect(x, y, w, h, rad, stroke=1 if bw else 0, fill=1 if bg else 0)
        else:
            c.rect(x, y, w, h, stroke=1 if bw else 0, fill=1 if bg else 0)
        c.restoreState()
    pad = float(st.get("pad", 0) or 0)
    ix, iy, iw, ih = x + pad, y + pad, w - 2 * pad, h - 2 * pad
    if t == "rect" or iw <= 0 or ih <= 0:
        return
    if t == "text":
        text = fill(b.get("text", ""), ctx)
        if st.get("upper"):
            text = re.sub(r"(^|>)([^<]+)", lambda m: m.group(1) + m.group(2).upper(), text)
        if not text.strip():
            return
        spacing = float(st.get("spacing", 0) or 0)
        if spacing and "\n" not in text and "<" not in text:  # letter-spaced single line (titles)
            size = float(st.get("size", 9))
            font = _font(st)
            from reportlab.pdfbase.pdfmetrics import stringWidth
            tw = stringWidth(text, font, size) + spacing * max(0, len(text) - 1)
            tx = {"center": ix + (iw - tw) / 2, "right": ix + iw - tw}.get(st.get("align"), ix)
            ty = {"middle": iy + (ih - size) / 2, "bottom": iy}.get(st.get("valign"), iy + ih - size)
            c.saveState()  # letter spacing is text state: keep it from leaking into later text
            to = c.beginText(tx, ty + size * 0.2)
            to.setFont(font, size)
            to.setCharSpace(spacing)
            to.setFillColor(color(st.get("color"), colors.HexColor("#1e293b")))
            to.textLine(text)
            to.setCharSpace(0)
            c.drawText(to)
            c.restoreState()
            return
        size = float(st.get("size", 9))
        para = Paragraph(text.replace("\n", "<br/>"), _para_style(st))
        _, ph = para.wrap(iw, ih)
        while ph > ih + 0.5 and st.get("fit", "shrink") == "shrink" and size > 5:
            size -= 0.5
            para = Paragraph(text.replace("\n", "<br/>"), _para_style(st, size))
            _, ph = para.wrap(iw, ih)
        va = st.get("valign", "top")
        py = iy + ih - ph if va == "top" else iy + (ih - ph) / 2 if va == "middle" else iy
        c.saveState()
        p = c.beginPath()
        p.rect(x, y, w, h)
        c.clipPath(p, stroke=0, fill=0)
        para.drawOn(c, ix, py)
        c.restoreState()
    elif t == "kv":  # label / value list: "Order # | {{order.code}}" per line; empty values skip the row (or show style.empty)
        size, lw = float(st.get("size", 8.8)), float(st.get("label_w", 0.95)) * inch
        step = size * float(st.get("lh", 1.7))
        kst = ParagraphStyle("k", fontName=FONT, fontSize=float(st.get("label_size", size - 0.8)), leading=size * 1.3,
                             textColor=color(st.get("label_color"), colors.HexColor("#64748b")))
        vst = ParagraphStyle("v", fontName=FONT if st.get("plain") else _font({"semi": True, **st}), fontSize=size, leading=size * 1.3,
                             textColor=color(st.get("color"), colors.HexColor("#1e293b")))
        yy = iy + ih
        for line in str(b.get("text") or "").split("\n"):
            if "|" not in line:
                continue
            k, v = line.split("|", 1)
            val = fill(v.strip(), ctx).strip() or escape(st.get("empty", "") or "")
            if not val:
                continue
            kp, vp = Paragraph(escape(k.strip()), kst), Paragraph(val.replace("\n", "<br/>"), vst)
            _, kh = kp.wrap(lw, ih)
            _, vh = vp.wrap(max(10, iw - lw), ih)
            if yy - max(kh, vh) < iy - 1:
                break
            kp.drawOn(c, ix, yy - kh)
            vp.drawOn(c, ix + lw, yy - vh)
            yy -= max(step, vh + size * 0.4)
    elif t == "table":
        _draw_table_block(c, b, st, ctx, ix, iy, iw, ih)
    elif t == "image":
        data = ctx.get("_logo") if b.get("src", "logo") == "logo" else None
        if not data or "," not in data:
            return
        try:
            img = ImageReader(io.BytesIO(base64.b64decode(data.split(",", 1)[1])))
        except Exception:
            return
        iw0, ih0 = img.getSize()
        s = min(iw / iw0, ih / ih0)
        dw, dh = iw0 * s, ih0 * s
        dx = {"center": ix + (iw - dw) / 2, "right": ix + iw - dw}.get(st.get("align", "left"), ix)
        c.drawImage(img, dx, iy + ih - dh, dw, dh, mask="auto")
    elif t == "barcode":
        value = fill(b.get("value", ""), ctx, markup=False).strip()
        if not value:
            return
        show = st.get("show_text", True)
        bar_h = ih - (11 if show else 0)
        bc_ = code128.Code128(value, barHeight=max(bar_h, 6), barWidth=1.0, humanReadable=False)
        s = min(1.0, iw / bc_.width) if bc_.width else 1
        c.saveState()
        c.setFillColor(colors.black)
        dx = {"center": ix + (iw - bc_.width * s) / 2, "right": ix + iw - bc_.width * s}.get(st.get("align", "left"), ix)
        c.translate(dx, iy + (11 if show else 0))
        c.scale(s, 1)
        bc_.drawOn(c, 0, 0)
        c.restoreState()
        if show:
            c.setFont(FONT, 8)
            c.setFillColor(colors.black)
            c.drawCentredString(dx + bc_.width * s / 2, iy + 1, value)
    elif t == "qr":
        value = fill(b.get("value", ""), ctx, markup=False).strip()
        if not value:
            return
        size = min(iw, ih)
        wdg = qr.QrCodeWidget(value)
        bx = wdg.getBounds()
        d = Drawing(size, size, transform=[size / (bx[2] - bx[0]), 0, 0, size / (bx[3] - bx[1]), 0, 0])
        d.add(wdg)
        renderPDF.draw(d, c, ix, iy + ih - size)


def _table_layout(b, st, rows, iw, size):
    """Column widths and wrapped cells at a font size -> (widths, header, [(row, cells, height)])."""
    cols = [col for col in (b.get("columns") or []) if col.get("key") and not col.get("hidden")]
    fixed = sum(float(col.get("w") or 0) * inch for col in cols)
    flex = [col for col in cols if not float(col.get("w") or 0)]
    widths = [float(col.get("w") or 0) * inch or max(20, (iw - fixed) / max(1, len(flex))) for col in cols]
    pad = float(st.get("pad", 4))
    hsize = float(st.get("header_size", max(6, size - 2)))
    hst = ParagraphStyle("th", fontName=FONT_BOLD, fontSize=hsize, leading=hsize * 1.2,
                         textColor=color(st.get("header_color"), colors.HexColor("#0f172a")))
    header = []
    for col, w in zip(cols, widths):
        para = Paragraph(escape(str(col.get("header", ""))).upper() if st.get("header_upper", True) else escape(str(col.get("header", ""))),
                         ParagraphStyle("h", parent=hst, alignment=ALIGN.get(col.get("align", "left"), TA_LEFT)))
        para.wrap(w - 2 * pad, 1000)
        header.append(para)
    head_h = max([p.height for p in header] + [hsize]) + 2 * pad
    laid = []
    for r in rows:
        cells = []
        for col, w in zip(cols, widths):
            big = col.get("big")
            fs = size * (float(st.get("big_scale", 1.35)) if big else 1)
            cst = ParagraphStyle("td", fontName=FONT_BOLD if (big or r.get("_hi")) else FONT, fontSize=fs, leading=fs * 1.12,
                                 textColor=color(st.get("color"), colors.HexColor("#0f172a")), alignment=ALIGN.get(col.get("align", "left"), TA_LEFT))
            para = Paragraph(escape(str(r.get(col["key"], "") or "")).replace("\n", "<br/>"), cst)
            para.wrap(w - 2 * pad, 10000)
            cells.append(para)
        laid.append((r, cells, max([p.height for p in cells] + [size]) + 2 * pad))
    return cols, widths, header, head_h, laid, pad


def _draw_table_block(c, b, st, ctx, ix, iy, iw, ih):
    """Rows from ctx[b.source]; the font shrinks (down to style.min_size) until every row fits. Rows that still don't fit
    are left in ctx["_overflow"] for the next label (render_labels prints a continuation label)."""
    rows = list(ctx.get(b.get("source") or "rows") or [])
    size, min_size = float(st.get("size", 11)), float(st.get("min_size", 7))
    while True:
        cols, widths, header, head_h, laid, pad = _table_layout(b, st, rows, iw, size)
        if head_h + sum(h for _, _, h in laid) <= ih + 0.5 or size <= min_size:
            break
        size -= 0.5
    rule = color(st.get("rule_color"), colors.HexColor("#334155"))
    grid = color(st.get("grid"), colors.HexColor("#94a3b8"))
    top = iy + ih
    c.saveState()
    c.setFillColor(color(st.get("header_bg"), colors.HexColor("#e5e7eb")))
    c.rect(ix, top - head_h, iw, head_h, stroke=0, fill=1)
    x = ix
    for para, w in zip(header, widths):
        para.drawOn(c, x + pad, top - pad - para.height)
        x += w
    c.setStrokeColor(rule)
    c.setLineWidth(float(st.get("header_rule_w", 1.2)))
    c.line(ix, top - head_h, ix + iw, top - head_h)
    y = top - head_h
    drawn = 0
    for r, cells, h in laid:
        if y - h < iy - 0.5:
            break
        if r.get("_hi"):  # this label's own pallet: a light band and a heavy outline
            c.setFillColor(color(st.get("hi_bg"), colors.HexColor("#e5e7eb")))
            c.rect(ix, y - h, iw, h, stroke=0, fill=1)
            c.setStrokeColor(colors.black)
            c.setLineWidth(1.6)
            c.rect(ix + 0.8, y - h + 0.8, iw - 1.6, h - 1.6, stroke=1, fill=0)
        x = ix
        for para, w in zip(cells, widths):
            para.drawOn(c, x + pad, y - pad - para.height)
            x += w
        y -= h
        c.setStrokeColor(grid)
        c.setLineWidth(0.6)
        c.line(ix, y, ix + iw, y)
        drawn += 1
    x = ix
    for w in widths[:-1]:  # column rules
        x += w
        c.setStrokeColor(grid)
        c.setLineWidth(0.6)
        c.line(x, top - head_h, x, y)
    if not rows:
        c.setFillColor(colors.HexColor("#64748b"))
        c.setFont(FONT, 9)
        c.drawString(ix + pad, top - head_h - 14, st.get("empty") or "Nothing to list.")
    c.restoreState()
    ctx["_overflow"] = rows[drawn:] if rows else []
    ctx["_overflow_source"] = b.get("source") or "rows"


def draw_band(c, band, ctx, ox, oy):
    for b in (band or {}).get("blocks", []):
        draw_block(c, b, ctx, ox, oy)


class BandFlowable(Flowable):
    """The summary band, placed after the table."""
    def __init__(self, band, ctx, width):
        super().__init__()
        self.band, self.ctx, self.width, self.height = band, ctx, width, float(band.get("h", 1)) * inch

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        draw_band(self.canv, self.band, self.ctx, 0, self.height)


class CheckBox(Flowable):
    def __init__(self, size=8):
        super().__init__()
        self.size = size

    def wrap(self, aw, ah):
        self.aw = aw
        return aw, self.size + 2

    def draw(self):
        self.canv.setStrokeColor(colors.HexColor("#1e293b"))
        self.canv.setLineWidth(0.8)
        self.canv.rect((self.aw - self.size) / 2, 1, self.size, self.size)


# ---------- the line-items table ----------
def build_table(tspec, rows, width):
    cols = [c for c in tspec.get("columns", []) if c.get("key")]
    if not cols:
        return None
    st = tspec.get("style") or {}
    size = float(st.get("size", 8.6))
    body = ParagraphStyle("td", fontName=FONT, fontSize=size, leading=size * 1.3, textColor=color(st.get("color"), colors.HexColor("#1e293b")))
    muted = ParagraphStyle("tdm", parent=body, fontSize=size - 0.8, leading=(size - 0.8) * 1.3, textColor=colors.HexColor("#64748b"))
    bold = ParagraphStyle("tdb", parent=body, fontName=FONT_SEMI, textColor=colors.HexColor("#0f172a"))
    head = ParagraphStyle("th", fontName=FONT_SEMI, fontSize=float(st.get("header_size", 7.5)), leading=float(st.get("header_size", 7.5)) * 1.3,
                          textColor=color(st.get("header_color"), colors.HexColor("#64748b")))
    fixed = sum(float(c.get("w") or 0) * inch for c in cols)
    flex = [c for c in cols if not float(c.get("w") or 0)]
    share = max(0.6 * inch, (width - fixed) / len(flex)) if flex else 0
    widths = [float(c.get("w") or 0) * inch or share for c in cols]

    def cell(c, r):
        k, a = c["key"], c.get("align") or ("right" if c["key"] in ("qty", "price", "amount", "ordered", "shipped", "backorder") else "left")
        if k == "check":
            return CheckBox()
        if k == "item_code_desc":
            out = [Paragraph(escape(r.get("item_code", "")), bold)]
            if r.get("description"):
                out.append(Paragraph(escape(r["description"]).replace("\n", "<br/>"), muted))
            return out
        v = str(r.get(k, ""))
        if v == "—" and "empty" in c:  # column setting: what an empty value prints as (e.g. "0" for no backorder)
            v = str(c["empty"])
        txt = escape(v).replace("\n", "<br/>")
        sty = ParagraphStyle("c", parent=bold if c.get("bold") or k == "amount" and st.get("bold_amount", True) else body,
                             alignment=ALIGN.get(a, TA_LEFT))
        return Paragraph(txt, sty)

    upper = st.get("header_upper")
    data = [[Paragraph(escape((c.get("header") or "").upper() if upper else c.get("header") or ""), ParagraphStyle("h", parent=head, alignment=ALIGN.get(
        c.get("align") or ("right" if c["key"] in ("qty", "price", "amount", "ordered", "shipped", "backorder") else "left"), TA_LEFT)))
             for c in cols]]
    data += [[cell(c, r) for c in cols] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    pad = float(st.get("pad", 5))
    cmds = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), pad), ("RIGHTPADDING", (0, 0), (-1, -1), pad),
            ("TOPPADDING", (0, 0), (-1, -1), pad), ("BOTTOMPADDING", (0, 0), (-1, -1), pad)]
    if st.get("header_bg"):
        cmds.append(("BACKGROUND", (0, 0), (-1, 0), color(st["header_bg"])))
    if st.get("header_rule"):
        cmds.append(("LINEBELOW", (0, 0), (-1, 0), float(st.get("header_rule_w", 1.2)), color(st["header_rule"])))
    if st.get("top_rule"):
        cmds.append(("LINEABOVE", (0, 0), (-1, 0), 0.8, color(st["top_rule"])))
    if st.get("row_rule", "#e2e8f0"):
        cmds.append(("LINEBELOW", (0, 1), (-1, -1), 0.5, color(st.get("row_rule", "#e2e8f0"))))
    if st.get("zebra"):
        for i in range(2, len(data), 2):
            cmds.append(("BACKGROUND", (0, i), (-1, i), color(st["zebra"])))
    if st.get("grid"):
        cmds.append(("GRID", (0, 0), (-1, -1), 0.5, color(st["grid"])))
    t.setStyle(TableStyle(cmds))
    return t


# ---------- whole documents ----------
def _canvas_class(spec, ctx, page_w, page_h, margin):
    footer = spec.get("footer") or {}
    fh = float(footer.get("h", 0)) * inch
    wm = ctx.get("_watermark")

    class TemplateCanvas(rl_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for i, state in enumerate(self._pages, 1):
                self.__dict__.update(state)
                if footer.get("blocks"):
                    draw_band(self, footer, {**ctx, "page": str(i), "pages": str(total)}, margin, margin + fh)
                if wm:
                    self.saveState()
                    self.setFont(FONT_BOLD, 96)
                    self.setFillColor(colors.HexColor("#b91c1c"))
                    self.setFillAlpha(0.08)
                    self.translate(page_w / 2, page_h / 2)
                    self.rotate(35)
                    self.drawCentredString(0, -30, wm)
                    self.restoreState()
                super().showPage()
            super().save()
    return TemplateCanvas


def render(spec, ctx, rows, title="Document") -> bytes:
    spec = visible_spec(spec)
    use_family(spec.get("font"))
    page = spec.get("page") or {}
    pw, ph, m = float(page.get("w", 8.5)) * inch, float(page.get("h", 11)) * inch, float(page.get("margin", 0.5)) * inch
    header, running, summary, footer = (spec.get(k) or {} for k in ("header", "running", "summary", "footer"))
    hh, rh, fh = float(header.get("h", 0)) * inch, float(running.get("h", 0)) * inch, float(footer.get("h", 0)) * inch
    width = pw - 2 * m
    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=(pw, ph), leftMargin=m, rightMargin=m, topMargin=m, bottomMargin=m, title=title)
    first = Frame(m, m + fh, width, ph - 2 * m - hh - fh, id="first", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    later = Frame(m, m + fh, width, ph - 2 * m - rh - fh, id="later", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([
        PageTemplate("first", [first], onPage=lambda c, d: draw_band(c, header, {**ctx, "page": str(d.page)}, m, ph - m), autoNextPageTemplate="later"),
        PageTemplate("later", [later], onPage=lambda c, d: draw_band(c, running, {**ctx, "page": str(d.page)}, m, ph - m)),
    ])
    story = []
    tspec = spec.get("table") or {}
    if tspec.get("omit_shipping"):  # shipping shown in the totals instead of as a line
        rows = [r for r in rows if not r.get("_shipping")]
    table = build_table(tspec, rows, width) if spec.get("table") else None
    if table is not None:
        story.append(table)
    pal = spec.get("pallets")
    if isinstance(pal, dict) and not pal.get("hidden") and ctx.get("_pallet_rows"):
        if pal.get("new_page"):
            story.append(PageBreak())
        else:
            story.append(Spacer(1, 10))
        if pal.get("heading"):
            hs = (pal.get("style") or {}).get("heading_size", 12)
            story += [Paragraph(escape(pal["heading"]), ParagraphStyle("ph", fontName=FONT_BOLD, fontSize=hs, leading=hs * 1.3, alignment=TA_CENTER,
                                                                       textColor=color((pal.get("style") or {}).get("heading_color"), colors.HexColor("#1f2d3a")))),
                      HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1f2d3a"), spaceBefore=3, spaceAfter=10)]
        ptable = build_table(pal, ctx["_pallet_rows"], width)
        if ptable is not None:
            story.append(ptable)
    if summary.get("blocks"):
        story += [Spacer(1, 6), BandFlowable(summary, ctx, width)]
    if not story:
        story.append(Spacer(1, 1))
    doc.build(story, canvasmaker=_canvas_class(spec, ctx, pw, ph, m))
    return buf.getvalue()


def render_labels(spec, contexts) -> bytes:
    """One page per label (box labels, address labels)."""
    spec = visible_spec(spec)
    use_family(spec.get("font"))
    page = spec.get("page") or {}
    pw, ph, m = float(page.get("w", 6)) * inch, float(page.get("h", 4)) * inch, float(page.get("margin", 0.15)) * inch
    band = spec.get("header") or {}
    pages = []
    for ctx in contexts or [{}]:
        # A table that doesn't fit carries on: measure on a scratch canvas, then number the sheets ("1 of 2").
        sheets, cur = [], copy.copy(ctx)
        for _ in range(50):
            scratch = rl_canvas.Canvas(io.BytesIO(), pagesize=(pw, ph))
            draw_band(scratch, band, cur, m, ph - m)
            sheets.append(cur)
            rest = cur.pop("_overflow", None)
            if not rest:
                break
            nxt = copy.copy(ctx)
            nxt[cur.pop("_overflow_source")] = rest
            cur = nxt
        for i, sh in enumerate(sheets, 1):
            sh = copy.copy(sh)
            if isinstance(sh.get("label"), dict):
                sh["label"] = {**sh["label"], "sheet": f"Sheet {i} of {len(sheets)}" if len(sheets) > 1 else "",
                               "continued": "continued" if i > 1 else ""}
            pages.append(sh)
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(pw, ph))
    for ctx in pages:
        draw_band(c, band, ctx, m, ph - m)
        c.showPage()
    c.save()
    return buf.getvalue()

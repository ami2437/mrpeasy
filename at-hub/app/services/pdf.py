"""Branded PDF documents (invoice, packing list) rendered server-side with ReportLab,
so the same file is what the user prints, downloads, and what gets emailed."""
import os
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, KeepTogether, Image,
)
from reportlab.lib.utils import ImageReader
from sqlalchemy.orm import Session

from app.models import CustomerOrder, Customer, Shipment, StockItem, Lot, Invoice, PurchaseOrder, Vendor
from app.services.crud import get_company_profile

NAVY = colors.HexColor("#1b2430")
ACCENT = colors.HexColor("#2563eb")
LIGHT = colors.HexColor("#f3f6fb")
ZEBRA = colors.HexColor("#f8fafc")
BORDER = colors.HexColor("#d9dee7")
MUTED = colors.HexColor("#5b6472")
GREEN = colors.HexColor("#15803d")
RED = colors.HexColor("#b91c1c")


def _register_fonts():
    """Use a TrueType font with full Unicode coverage when the OS has one (customer names,
    accented addresses); fall back to the built-in Helvetica otherwise."""
    candidates = [
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ariali.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
    ]
    for regular, bold, italic in candidates:
        if all(os.path.exists(p) for p in (regular, bold, italic)):
            try:
                pdfmetrics.registerFont(TTFont("DocSans", regular))
                pdfmetrics.registerFont(TTFont("DocSans-Bold", bold))
                pdfmetrics.registerFont(TTFont("DocSans-Italic", italic))
                pdfmetrics.registerFontFamily("DocSans", normal="DocSans", bold="DocSans-Bold", italic="DocSans-Italic")
                return "DocSans", "DocSans-Bold", "DocSans-Italic"
            except Exception:
                continue
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


FONT, FONT_BOLD, FONT_ITALIC = _register_fonts()


def _style(name, **kw):
    base = dict(fontName=FONT, fontSize=9, leading=12, textColor=NAVY)
    base.update(kw)
    return ParagraphStyle(name, **base)


S = {
    "company": _style("company", fontName=FONT_BOLD, fontSize=15, leading=19),
    "body": _style("body"),
    "small": _style("small", fontSize=8, leading=10.5, textColor=MUTED),
    "title": _style("title", fontName=FONT_BOLD, fontSize=26, leading=30, textColor=ACCENT, alignment=TA_RIGHT),
    "docno": _style("docno", fontName=FONT_BOLD, fontSize=11, leading=14, alignment=TA_RIGHT),
    "label": _style("label", fontName=FONT_BOLD, fontSize=7.5, leading=10, textColor=MUTED),
    "party": _style("party", fontName=FONT_BOLD, fontSize=11, leading=14),
    "th": _style("th", fontName=FONT_BOLD, fontSize=8, leading=10, textColor=colors.white),
    "th_r": _style("th_r", fontName=FONT_BOLD, fontSize=8, leading=10, textColor=colors.white, alignment=TA_RIGHT),
    "td": _style("td", fontSize=9, leading=11.5),
    "td_r": _style("td_r", fontSize=9, leading=11.5, alignment=TA_RIGHT),
    "td_muted": _style("td_muted", fontSize=7.5, leading=10, textColor=MUTED),
    "meta_k": _style("meta_k", fontSize=8.5, leading=11, textColor=MUTED),
    "meta_v": _style("meta_v", fontName=FONT_BOLD, fontSize=8.5, leading=11, alignment=TA_RIGHT),
    "total_k": _style("total_k", fontSize=9.5, leading=12),
    "total_v": _style("total_v", fontSize=9.5, leading=12, alignment=TA_RIGHT),
    "grand_k": _style("grand_k", fontName=FONT_BOLD, fontSize=11, leading=14, textColor=colors.white),
    "grand_v": _style("grand_v", fontName=FONT_BOLD, fontSize=12, leading=14, textColor=colors.white, alignment=TA_RIGHT),
    "thanks": _style("thanks", fontName=FONT_ITALIC, fontSize=10, leading=13, textColor=ACCENT),
}


# ---- formatting ----
def money(n) -> str:
    n = n or 0
    return f"-${abs(n):,.2f}" if n < 0 else f"${n:,.2f}"


def price(n) -> str:
    """Unit price with up to 5 decimals, never fewer than 2: 0.21375 -> $0.21375, 3.5 -> $3.50."""
    s = f"{(n or 0):,.5f}".rstrip("0")
    whole, _, frac = s.partition(".")
    return f"${whole}.{frac.ljust(2, '0')}"


def qty(n) -> str:
    n = n or 0
    return f"{n:,.0f}" if abs(n - round(n)) < 1e-9 else f"{n:,.3f}".rstrip("0")


def date(d) -> str:
    return d.strftime("%b %d, %Y") if d else ""


def p(text, style="body") -> Paragraph:
    return Paragraph(escape(str(text or "")).replace("\n", "<br/>"), S[style])


# ---- page furniture ----
def _numbered_canvas(footer_text: str, watermark: str = None, watermark_color=None):
    """Canvas that knows the page count, so every page gets 'Page x of y' plus the footer,
    and optionally a diagonal PAID / VOID / DRAFT watermark."""
    class NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_pages = []

        def showPage(self):
            self._saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_pages)
            for state in self._saved_pages:
                self.__dict__.update(state)
                self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self, total):
            w, h = letter
            if watermark:
                self.saveState()
                self.setFont(FONT_BOLD, 96)
                self.setFillColor(watermark_color or MUTED)
                self.setFillAlpha(0.08)
                self.translate(w / 2, h / 2)
                self.rotate(35)
                self.drawCentredString(0, -30, watermark)
                self.restoreState()
            self.saveState()
            self.setStrokeColor(BORDER)
            self.setLineWidth(0.6)
            self.line(0.6 * inch, 0.55 * inch, w - 0.6 * inch, 0.55 * inch)
            self.setFont(FONT, 7.5)
            self.setFillColor(MUTED)
            self.drawString(0.6 * inch, 0.38 * inch, footer_text)
            self.drawRightString(w - 0.6 * inch, 0.38 * inch, f"Page {self._pageNumber} of {total}")
            self.restoreState()

    return NumberedCanvas


def _build(story, footer_text, title, watermark=None, watermark_color=None) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter, title=title,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.55 * inch, bottomMargin=0.8 * inch,
    )
    doc.build(story, canvasmaker=_numbered_canvas(footer_text, watermark, watermark_color))
    return buf.getvalue()


def _footer_text(company) -> str:
    return "  ·  ".join(x for x in [company.name, company.email, company.phone, company.website] if x)


def logo_flowable(company, max_w: float = 1.6 * inch, max_h: float = 0.7 * inch):
    """The uploaded company logo scaled to fit max_w x max_h, or None."""
    from app.routes.company import logo_bytes
    data, _ = logo_bytes(company)
    if not data:
        return None
    try:
        w, h = ImageReader(BytesIO(data)).getSize()
    except Exception:
        return None
    scale = min(max_w / w, max_h / h)
    img = Image(BytesIO(data), width=w * scale, height=h * scale)
    img.hAlign = "LEFT"
    return img


def fit_width(texts, minimum: float, maximum: float = 2.2 * inch, style: str = "td") -> float:
    """Column width that keeps the longest of `texts` on one line (plus cell padding)."""
    st = S[style]
    longest = max((pdfmetrics.stringWidth(str(t or ""), st.fontName, st.fontSize) for t in texts), default=0)
    return min(maximum, max(minimum, longest + 16))


def invoice_line_description(line, item) -> str:
    """Older invoice lines stored "CODE — title" as the description; the code now has its own column."""
    desc = line.description or ""
    if item and desc.startswith(f"{item.code} — "):
        return desc[len(item.code) + 3:]
    return desc


def address_lines(text):
    return [l.strip() for l in (text or "").splitlines() if l.strip()]


def _header(company, title: str, doc_number: str, status_label: str = None, status_color=None):
    left = []
    logo = logo_flowable(company)
    if logo:
        left += [logo, Spacer(1, 6)]
    left.append(p(company.name, "company"))
    if company.address:
        left.append(p(company.address, "small"))
    contact = "  ·  ".join(x for x in [company.phone, company.email, company.website] if x)
    if contact:
        left.append(p(contact, "small"))
    if company.tax_id:
        left.append(p(f"Tax ID: {company.tax_id}", "small"))

    right = [p(title, "title"), p(doc_number, "docno")]
    if status_label:
        right.append(Spacer(1, 4))
        label = status_label.upper()
        pill = Table([[Paragraph(escape(label), _style(
            "pill", fontName=FONT_BOLD, fontSize=7.5, leading=9, textColor=colors.white))]],
            colWidths=[pdfmetrics.stringWidth(label, FONT_BOLD, 7.5) + 16])
        pill.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), status_color or MUTED),
            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ]))
        pill.hAlign = "RIGHT"
        right.append(pill)

    t = Table([[left, right]], colWidths=[4.4 * inch, 2.9 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    bar = Table([[""]], colWidths=[7.3 * inch], rowHeights=[2.5])
    bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]))
    return [t, Spacer(1, 10), bar, Spacer(1, 14)]


def _party_box(label: str, name: str, lines):
    content = [p(label, "label"), Spacer(1, 3), p(name, "party")]
    content += [p(l, "body") for l in lines if l]
    return content


def _meta_table(rows):
    rows = [(k, v) for k, v in rows if v]
    t = Table([[p(k, "meta_k"), p(v, "meta_v")] for k, v in rows], colWidths=[1.15 * inch, 1.65 * inch])
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, BORDER),
    ]))
    return t


def _two_boxes(left, right):
    t = Table([[left, right]], colWidths=[4.2 * inch, 3.1 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, 0), LIGHT),
        ("BOX", (0, 0), (0, 0), 0.5, BORDER),
        ("LEFTPADDING", (0, 0), (0, 0), 12), ("RIGHTPADDING", (0, 0), (0, 0), 12),
        ("TOPPADDING", (0, 0), (0, 0), 10), ("BOTTOMPADDING", (0, 0), (0, 0), 12),
        ("LEFTPADDING", (1, 0), (1, 0), 18), ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
    ]))
    return t


def _data_table(header, rows, col_widths, right_cols=(), repeat=1):
    head = [p(h, "th_r" if i in right_cols else "th") for i, h in enumerate(header)]
    body = []
    for r in rows:
        body.append([c if not isinstance(c, str) else p(c, "td_r" if i in right_cols else "td") for i, c in enumerate(r)])
    t = Table([head] + body, colWidths=col_widths, repeatRows=repeat)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 6), ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 5), ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, BORDER),
        ("LINEBELOW", (0, -1), (-1, -1), 1, NAVY),
    ]
    for i in range(1, len(body) + 1):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    t.setStyle(TableStyle(style))
    return t


def _notes_box(sections):
    """sections: [(label, text)] -- rendered as one bordered box with an accent edge."""
    content = []
    for label, text in sections:
        if not text:
            continue
        if content:
            content.append(Spacer(1, 8))
        content += [p(label, "label"), Spacer(1, 2), p(text, "body")]
    if not content:
        return []
    t = Table([[content]], colWidths=[7.3 * inch])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 3, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return [Spacer(1, 18), KeepTogether(t)]


# ---- invoice ----
INVOICE_STATUS_COLORS = {"draft": MUTED, "sent": ACCENT, "paid": GREEN, "void": RED}


def invoice_pdf(db: Session, invoice: Invoice) -> bytes:
    company = get_company_profile(db)
    customer = db.query(Customer).filter(Customer.id == invoice.customer_id).first()
    order = db.query(CustomerOrder).filter(CustomerOrder.id == invoice.order_id).first() if invoice.order_id else None
    shipment = db.query(Shipment).filter(Shipment.id == invoice.shipment_id).first() if invoice.shipment_id else None

    story = _header(company, "INVOICE", invoice.code, invoice.status,
                    INVOICE_STATUS_COLORS.get(invoice.status))

    ship_addr = (order.ship_to_address if order else None) or (customer.shipping_address if customer else None)
    bill_addr = customer.address if customer else None
    bill_to = _party_box("BILL TO", customer.name if customer else "", [
        f"Attn: {customer.contact_name}" if customer and customer.contact_name else None,
        *address_lines(bill_addr),
        customer.phone if customer else None,
        customer.email if customer else None,
    ])
    if ship_addr and ship_addr.strip() != (bill_addr or "").strip():
        bill_to += [Spacer(1, 8), p("SHIP TO", "label"), Spacer(1, 3)] + [p(l, "body") for l in address_lines(ship_addr)]
    meta = _meta_table([
        ("Invoice date", date(invoice.invoice_date)),
        ("Due date", date(invoice.due_date)),
        ("Order #", order.code if order else None),
        ("Customer PO #", order.po_number if order else None),
        ("Job #", order.job_number if order else None),
        ("Shipment #", shipment.code if shipment else None),
        ("Shipped", date(shipment.ship_date) if shipment else None),
        ("Delivered", date(shipment.delivered_at) if shipment else None),
    ])
    story += [_two_boxes(bill_to, meta), Spacer(1, 18)]

    line_items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_({l.item_id for l in invoice.lines if l.item_id})).all()}
    rows = []
    for i, l in enumerate(invoice.lines, 1):
        item = line_items.get(l.item_id)
        rows.append([str(i), p(item.code if item else "", "td"), p(invoice_line_description(l, item), "td"),
                     qty(l.quantity), price(l.unit_price), money(l.quantity * l.unit_price)])
    code_w = fit_width([i.code for i in line_items.values()], 0.9 * inch)
    story.append(_data_table(
        ["#", "Item #", "Description", "Qty", "Unit price", "Amount"], rows,
        [0.35 * inch, code_w, 7.3 * inch - 0.35 * inch - code_w - 3.1 * inch, 0.9 * inch, 1.05 * inch, 1.15 * inch], right_cols=(3, 4, 5),
    ))

    total_rows = [[p("Subtotal", "total_k"), p(money(invoice.total), "total_v")]]
    if invoice.amount_paid > 0:
        total_rows.append([p("Paid", "total_k"), p(money(-invoice.amount_paid), "total_v")])
    grand_label = "Balance due" if invoice.amount_paid > 0 else "Total due"
    total_rows.append([p(grand_label, "grand_k"), p(money(invoice.balance), "grand_v")])
    totals = Table(total_rows, colWidths=[1.5 * inch, 1.4 * inch])
    totals.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, BORDER),
        ("BACKGROUND", (0, -1), (-1, -1), ACCENT),
        ("TOPPADDING", (0, -1), (-1, -1), 7), ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
    ]))
    totals.hAlign = "RIGHT"
    story += [Spacer(1, 10), KeepTogether(totals)]

    if invoice.payments:
        pay_rows = [[date(pm.paid_date), pm.method or "", pm.reference or "", money(pm.amount)] for pm in invoice.payments]
        story += [Spacer(1, 16), p("PAYMENTS RECEIVED", "label"), Spacer(1, 4),
                  _data_table(["Date", "Method", "Reference", "Amount"], pay_rows,
                              [1.4 * inch, 1.6 * inch, 3.0 * inch, 1.3 * inch], right_cols=(3,))]

    free_text = invoice.free_text if invoice.free_text != "Generated via AT-HUB" else None
    story += _notes_box([("NOTES", free_text), ("PAYMENT INSTRUCTIONS & TERMS", company.invoice_notes)])
    story += [Spacer(1, 18), p("Thank you for your business!", "thanks")]

    watermark = {"paid": ("PAID", GREEN), "void": ("VOID", RED)}.get(invoice.status)
    return _build(story, _footer_text(company), f"Invoice {invoice.code}",
                  watermark[0] if watermark else None, watermark[1] if watermark else None)


# ---- packing list ----
def packing_list_pdf(db: Session, shipment: Shipment, include_boxes: bool = True, include_pallets: bool = False,
                     include_lots: bool = False) -> bytes:
    """include_boxes adds the per-line box breakdown; include_pallets adds a Pallet # column
    plus the pallet weight/dimensions section; include_lots adds the Lot # column -- all chosen
    by the user at print time."""
    company = get_company_profile(db)
    order = db.query(CustomerOrder).filter(CustomerOrder.id == shipment.order_id).first()
    customer = db.query(Customer).filter(Customer.id == order.customer_id).first() if order else None
    items = {i.id: i for i in db.query(StockItem).filter(
        StockItem.id.in_({l.item_id for l in shipment.lines} | {b.item_id for b in shipment.boxes})).all()}
    lots = {l.id: l for l in db.query(Lot).filter(Lot.id.in_({l.lot_id for l in shipment.lines if l.lot_id})).all()}

    story = _header(company, "PACKING LIST", shipment.code, shipment.status,
                    {"shipped": GREEN, "delivered": GREEN, "invoiced": GREEN, "cancelled": RED}.get(shipment.status, ACCENT))

    ship_addr = ((order.ship_to_address if order else None)
                 or (customer.shipping_address if customer else None) or (customer.address if customer else None))
    ship_to = _party_box("SHIP TO", customer.name if customer else "", [
        f"Attn: {customer.contact_name}" if customer and customer.contact_name else None,
        *address_lines(ship_addr),
        customer.phone if customer else None,
    ])
    meta = _meta_table([
        ("Shipment #", shipment.code),
        ("Order #", order.code if order else None),
        ("Customer PO #", order.po_number if order else None),
        ("Job #", order.job_number if order else None),
        ("Ship date", date(shipment.ship_date)),
        ("Carrier", shipment.carrier),
        ("Tracking #", shipment.tracking_number),
    ])
    story += [_two_boxes(ship_to, meta), Spacer(1, 18)]

    # One row per order line -- the same item on two lines is listed twice, never merged.
    shipped_by_line, lots_by_line, order_lines = {}, {}, {}
    for l in shipment.lines:
        shipped_by_line[l.order_line_id] = shipped_by_line.get(l.order_line_id, 0) + l.quantity
        order_lines[l.order_line_id] = l.order_line
        if l.lot_id in lots:
            lots_by_line.setdefault(l.order_line_id, []).append(lots[l.lot_id].lot_code)

    rows, total_units, box_texts = [], 0, []
    for line_id in sorted(shipped_by_line, key=lambda i: (order_lines[i].line_no or 0, i)):
        ol, shipped = order_lines[line_id], shipped_by_line[line_id]
        item = items.get(ol.item_id)
        backordered = max(0, ol.quantity - ol.shipped_quantity - ol.booked_quantity)
        by_qty = {}
        for b in shipment.boxes:
            if b.order_line_id == line_id:
                by_qty[b.quantity_in_box] = by_qty.get(b.quantity_in_box, 0) + 1
        boxes = "\n".join(f"{n} Box × {qty(q)}" for q, n in sorted(by_qty.items(), reverse=True)) or "—"
        line_pallets = ", ".join(dict.fromkeys(b.pallet_number for b in shipment.boxes
                                               if b.order_line_id == line_id and b.pallet_number)) or "—"
        row = [str(ol.line_no or ""), p(item.code if item else ol.item_id, "td"), p(item.title if item else "", "td")]
        if include_lots:
            row.append(p(", ".join(dict.fromkeys(lots_by_line.get(line_id, []))) or "—", "td_muted"))
        row += [qty(ol.quantity), qty(shipped), qty(backordered)]
        if include_boxes:
            row.append(p(boxes, "td"))
            box_texts.append(boxes)
        if include_pallets:
            row.append(p(line_pallets, "td"))
        rows.append(row)
        total_units += shipped
    # Fixed columns, then Boxes sized to its longest line ("12 Box × 1,000" never wraps);
    # Description takes whatever width is left.
    headers = ["Line", "Item #", "Description"] + (["Lot #"] if include_lots else []) + ["Ordered", "Shipped", "Backorder"]
    code_w = fit_width([i.code for i in items.values()], 0.85 * inch)
    widths = [0.4 * inch, code_w, 0] + ([0.85 * inch] if include_lots else []) + [0.62 * inch, 0.62 * inch, 0.72 * inch]
    num_cols = tuple(range(len(headers) - 3, len(headers)))
    if include_boxes:
        td = S["td"]
        longest = max((pdfmetrics.stringWidth(line, td.fontName, td.fontSize)
                       for text in box_texts for line in text.split("\n")), default=0)
        headers.append("Boxes")
        widths.append(max(0.9 * inch, longest + 16))
    if include_pallets:
        headers.append("Pallet #")
        widths.append(0.75 * inch)
    widths[2] = max(1.0 * inch, 7.3 * inch - sum(widths))
    story.append(_data_table(headers, rows, widths, right_cols=num_cols))

    pallets = {}
    for b in shipment.boxes:
        key = b.pallet_number or "Unassigned"
        entry = pallets.setdefault(key, {"items": [], "boxes": 0})
        code = items[b.item_id].code if b.item_id in items else str(b.item_id)
        if code not in entry["items"]:
            entry["items"].append(code)
        entry["boxes"] += 1
    total_weight = sum(pw.weight or 0 for pw in shipment.pallets)
    pallet_count = len([k for k in pallets if k != "Unassigned"])

    cells = [[p("TOTAL UNITS", "label"), p(qty(total_units), "party")]]
    if include_boxes:
        cells.append([p("TOTAL BOXES", "label"), p(str(len(shipment.boxes)) if shipment.boxes else "—", "party")])
    if include_pallets:
        cells.append([p("PALLETS", "label"), p(str(pallet_count) if pallet_count else "—", "party")])
        cells.append([p("TOTAL WEIGHT", "label"), p(f"{total_weight:,.1f} lbs" if total_weight else "—", "party")])
    summary = Table([cells], colWidths=[7.3 * inch / len(cells)] * len(cells))
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT), ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("LINEBEFORE", (1, 0), (-1, -1), 0.5, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story += [Spacer(1, 14), summary]

    if include_pallets and pallet_count:
        prow = []
        for key, entry in pallets.items():
            info = next((pw for pw in shipment.pallets if pw.pallet_number == key), None)
            if key == "Unassigned":
                continue
            prow.append([key, p(", ".join(entry["items"]), "td"), str(entry["boxes"]),
                         f"{info.weight:,.1f}" if info and info.weight else "—", (info.dimensions if info else None) or "—"])
        story += [Spacer(1, 16), p("PALLETS", "label"), Spacer(1, 4),
                  _data_table(["Pallet #", "Items", "Boxes", "Weight (lbs)", "Dimensions (L x W x H in)"], prow,
                              [1.0 * inch, 3.0 * inch, 0.8 * inch, 1.1 * inch, 1.4 * inch], right_cols=(2, 3))]

    story += _notes_box([("NOTES", shipment.notes)])

    sig = Table([["", "", "", ""], [p("Packed by / date", "small"), "", p("Received by / date", "small"), ""]],
                colWidths=[3.3 * inch, 0.7 * inch, 3.3 * inch, 0.0001 * inch], rowHeights=[28, 14])
    sig.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (0, 0), 0.8, NAVY), ("LINEBELOW", (2, 0), (2, 0), 0.8, NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [Spacer(1, 30), KeepTogether(sig)]

    return _build(story, _footer_text(company), f"Packing list {shipment.code}")


# ---- purchase order ----
def purchase_order_pdf(db: Session, po: PurchaseOrder, for_vendor: bool = False) -> bytes:
    """for_vendor=True is the copy that goes out: lines show only the vendor's part # and
    description -- our own item numbers stay internal. The internal copy shows both."""
    company = get_company_profile(db)
    vendor = db.query(Vendor).filter(Vendor.id == po.vendor_id).first()
    items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_({l.item_id for l in po.lines})).all()}

    status = None if for_vendor else po.status.replace("_", " ")
    story = _header(company, "PURCHASE ORDER", po.code, status,
                    {"received": GREEN, "cancelled": RED}.get(po.status, ACCENT))

    vendor_box = _party_box("VENDOR", vendor.name if vendor else "", [
        f"Attn: {vendor.contact_name}" if vendor and vendor.contact_name else None,
        *address_lines(vendor.address if vendor else None),
        vendor.phone if vendor else None,
        vendor.email if vendor else None,
    ])
    vendor_box += [Spacer(1, 8), p("SHIP TO", "label"), Spacer(1, 3), p(company.name, "body")]
    vendor_box += [p(l, "body") for l in address_lines(company.address)]
    meta = _meta_table([
        ("PO #", po.code),
        ("Order date", date(po.order_date)),
        ("Required by", date(po.expected_date)),
        ("Contact", company.email),
    ])
    story += [_two_boxes(vendor_box, meta), Spacer(1, 18)]

    rows, total = [], 0
    for i, l in enumerate(po.lines, 1):
        item = items.get(l.item_id)
        description = l.vendor_description or (item.title if item else "")
        amount = l.quantity * l.unit_cost
        total += amount
        if for_vendor:
            rows.append([str(i), p(l.vendor_item_code or "—", "td"), p(description, "td"),
                         qty(l.quantity), price(l.unit_cost), money(amount)])
        else:
            rows.append([str(i), p(item.code if item else l.item_id, "td"), p(l.vendor_item_code or "—", "td"),
                         p(description, "td"), qty(l.quantity), price(l.unit_cost), money(amount)])
    if for_vendor:
        story.append(_data_table(["#", "Item number", "Description", "Qty", "Unit price", "Amount"], rows,
                                 [0.35 * inch, 1.3 * inch, 2.75 * inch, 0.8 * inch, 1.0 * inch, 1.1 * inch], right_cols=(3, 4, 5)))
    else:
        story.append(_data_table(["#", "Our item #", "Vendor item #", "Description", "Qty", "Unit cost", "Amount"], rows,
                                 [0.35 * inch, 1.3 * inch, 1.0 * inch, 1.8 * inch, 0.7 * inch, 1.0 * inch, 1.15 * inch],
                                 right_cols=(4, 5, 6)))

    totals = Table([[p("Total", "grand_k"), p(money(total), "grand_v")]], colWidths=[1.5 * inch, 1.4 * inch])
    totals.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    totals.hAlign = "RIGHT"
    story += [Spacer(1, 10), KeepTogether(totals)]
    story += _notes_box([("NOTES", po.notes)])
    if for_vendor:
        story += [Spacer(1, 18), p(f"Please reference {po.code} on all shipments, packing lists and invoices, "
                                   f"and include material test reports where applicable.", "body")]
    return _build(story, _footer_text(company), f"Purchase order {po.code}",
                  "CANCELLED" if po.status == "cancelled" else None, RED)

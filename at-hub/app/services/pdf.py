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

from app.models import CustomerOrder, Customer, Shipment, ShipmentLine, StockItem, Lot, Invoice, PurchaseOrder, Vendor
from app.services.crud import get_company_profile

NAVY = colors.HexColor("#1b2430")
ACCENT = colors.HexColor("#2563eb")
LIGHT = colors.HexColor("#f3f6fb")
ZEBRA = colors.HexColor("#f8fafc")
BORDER = colors.HexColor("#d9dee7")
# Fills stay light so a black-and-white printer keeps every word readable (dark fills print near-black).
HEAD_BG = colors.HexColor("#e5e7eb")
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
    "th": _style("th", fontName=FONT_BOLD, fontSize=8, leading=10, textColor=NAVY),
    "th_r": _style("th_r", fontName=FONT_BOLD, fontSize=8, leading=10, textColor=NAVY, alignment=TA_RIGHT),
    "td": _style("td", fontSize=9, leading=11.5),
    "td_r": _style("td_r", fontSize=9, leading=11.5, alignment=TA_RIGHT),
    "td_muted": _style("td_muted", fontSize=7.5, leading=10, textColor=MUTED),
    "meta_k": _style("meta_k", fontSize=8.5, leading=11, textColor=MUTED),
    "meta_v": _style("meta_v", fontName=FONT_BOLD, fontSize=8.5, leading=11, alignment=TA_RIGHT),
    "total_k": _style("total_k", fontSize=9.5, leading=12),
    "total_v": _style("total_v", fontSize=9.5, leading=12, alignment=TA_RIGHT),
    "grand_k": _style("grand_k", fontName=FONT_BOLD, fontSize=11, leading=14, textColor=NAVY),
    "grand_v": _style("grand_v", fontName=FONT_BOLD, fontSize=12, leading=14, textColor=NAVY, alignment=TA_RIGHT),
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


def described(text, line, show_notes: bool = True) -> Paragraph:
    """A line's description with its note (if any) in small italics underneath. A note marked
    "don't print" -- or every note when the user unticked notes at print time -- stays off."""
    html = escape(str(text or "")).replace(chr(10), "<br/>")
    note = (getattr(line, "notes", None) or "").strip() if line is not None else ""
    if note and show_notes and getattr(line, "print_notes", True) is not False:
        html += f'<br/><font size="7.5" color="#4b5563"><i>Note: {escape(note).replace(chr(10), "<br/>")}</i></font>'
    return Paragraph(html, S["td"])


# ---- page furniture ----
def _numbered_canvas(footer_text: str, watermark: str = None, watermark_color=None,
                     trace_text: str = None, signature: str = None):
    """Canvas that knows the page count, so every page gets 'Page x of y' plus the footer,
    and optionally a diagonal PAID / VOID / DRAFT watermark. trace_text (bold, every page)
    identifies the document if a page gets separated; signature draws a sign-here line in
    the bottom-right corner of the last page."""
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
            self.setFillColor(MUTED)
            if trace_text:
                self.setFont(FONT_BOLD, 8)
                self.setFillColor(NAVY)
                self.drawString(0.6 * inch, 0.38 * inch, trace_text)
                self.setFillColor(MUTED)
                self.setFont(FONT, 7)
                self.drawString(0.6 * inch, 0.24 * inch, footer_text)
            else:
                self.setFont(FONT, 7.5)
                self.drawString(0.6 * inch, 0.38 * inch, footer_text)
            self.setFont(FONT, 7.5)
            self.drawRightString(w - 0.6 * inch, 0.38 * inch, f"Page {self._pageNumber} of {total}")
            if signature and self._pageNumber == total:
                x1, x2, y = w - 0.6 * inch - 3.0 * inch, w - 0.6 * inch, 0.95 * inch
                self.setStrokeColor(NAVY)
                self.setLineWidth(0.8)
                self.line(x1, y, x2, y)
                self.setFont(FONT, 8)
                self.drawString(x1, y - 11, signature)
            self.restoreState()

    return NumberedCanvas


def _build(story, footer_text, title, watermark=None, watermark_color=None, trace_text=None, signature=None) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter, title=title,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.55 * inch,
        bottomMargin=(1.45 if signature else 0.8) * inch,  # room for the sign-here line
    )
    doc.build(story, canvasmaker=_numbered_canvas(footer_text, watermark, watermark_color, trace_text, signature))
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
    """Address text -> lines; "N/A" when nothing is on file."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return lines or ["N/A"]


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
            "pill", fontName=FONT_BOLD, fontSize=7.5, leading=9, textColor=status_color or MUTED))]],
            colWidths=[pdfmetrics.stringWidth(label, FONT_BOLD, 7.5) + 16])
        pill.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 1, status_color or MUTED),  # outlined, not filled: prints on any printer
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
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG), ("LINEBELOW", (0, 0), (-1, 0), 1, NAVY),
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


def _designed(db, doc_type, record, customer_id=None, **options):
    """The default Template Designer layout for this document, if one is set (else None: built-in layout)."""
    from app.services.templates import default_for, render_record
    tpl = default_for(db, doc_type, customer_id)
    return render_record(db, tpl, doc_type, record, options) if tpl else None


def invoice_pdf(db: Session, invoice: Invoice, show_notes: bool = True) -> bytes:
    designed = _designed(db, "invoice", invoice, invoice.customer_id, show_notes=show_notes)
    if designed:
        return designed
    company = get_company_profile(db)
    customer = db.query(Customer).filter(Customer.id == invoice.customer_id).first()
    order = db.query(CustomerOrder).filter(CustomerOrder.id == invoice.order_id).first() if invoice.order_id else None
    shipments = list(invoice.shipments) or (
        [db.query(Shipment).filter(Shipment.id == invoice.shipment_id).first()] if invoice.shipment_id else [])
    shipments = [s for s in shipments if s]
    combined = len(shipments) > 1

    story = _header(company, "INVOICE", invoice.code)  # customer-facing: no internal status label

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
    ] + ([("Shipments", ", ".join(s.code for s in shipments))] if combined else [
        ("Shipment #", shipments[0].code if shipments else None),
        ("Shipped", date(shipments[0].ship_date) if shipments else None),
        ("Delivered", date(shipments[0].delivered_at) if shipments else None),
    ]))
    story += [_two_boxes(bill_to, meta), Spacer(1, 18)]

    line_items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_({l.item_id for l in invoice.lines if l.item_id})).all()}
    # $0 lines (free samples, no-charge items) stay off the customer's copy unless asked for.
    printed = [l for l in invoice.lines if invoice.print_zero_lines or abs(l.amount) >= 0.005]
    by_id = {s.id: s for s in shipments}
    rows = []
    for i, l in enumerate(printed, 1):
        item = line_items.get(l.item_id)
        row = [str(i), p(item.code if item else "", "td"), described(invoice_line_description(l, item), l, show_notes)]
        if combined:
            sh = by_id.get(l.shipment_id)
            row.append(p(f"{sh.code}\n{date(sh.delivered_at or sh.ship_date)}" if sh else "", "td_muted"))
        rows.append(row + [qty(l.quantity), price(l.unit_price), money(l.amount)])
    code_w = fit_width([i.code for i in line_items.values()], 0.9 * inch)
    ship_w = 1.0 * inch if combined else 0
    story.append(_data_table(
        ["#", "Item #", "Description"] + (["Shipment"] if combined else []) + ["Qty", "Unit price", "Amount"], rows,
        [0.35 * inch, code_w, 7.3 * inch - 0.35 * inch - code_w - 3.1 * inch - ship_w] + ([ship_w] if combined else [])
        + [0.9 * inch, 1.05 * inch, 1.15 * inch],
        right_cols=(4, 5, 6) if combined else (3, 4, 5),
    ))

    # Customer copy: what was billed, never what has been paid or how.
    total_rows = [[p("Subtotal", "total_k"), p(money(invoice.total), "total_v")],
                  [p("Total due", "grand_k"), p(money(invoice.total), "grand_v")]]
    totals = Table(total_rows, colWidths=[1.5 * inch, 1.4 * inch])
    totals.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, BORDER),
        ("BACKGROUND", (0, -1), (-1, -1), HEAD_BG), ("BOX", (0, -1), (-1, -1), 1, NAVY),
        ("TOPPADDING", (0, -1), (-1, -1), 7), ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
    ]))
    totals.hAlign = "RIGHT"
    story += [Spacer(1, 10), KeepTogether(totals)]

    free_text = invoice.free_text if invoice.free_text != "Generated via AT-HUB" else None
    story += _notes_box([("NOTES", free_text), ("PAYMENT INSTRUCTIONS & TERMS", company.invoice_notes)])
    story += [Spacer(1, 18), p("Thank you for your business!", "thanks")]

    watermark = {"void": ("VOID", RED)}.get(invoice.status)
    return _build(story, _footer_text(company), f"Invoice {invoice.code}",
                  watermark[0] if watermark else None, watermark[1] if watermark else None)


# ---- packing list ----
def packing_list_pdf(db: Session, shipment: Shipment, include_boxes: bool = True, include_pallets: bool = False,
                     include_lots: bool = False, show_notes: bool = True, include_pallet_boxes: bool = False) -> bytes:
    """include_boxes adds the per-line box breakdown; include_pallets adds a Pallet # column
    plus the pallet weight/dimensions section; include_lots adds the Lot # column; include_pallet_boxes adds the
    box count per pallet -- all chosen by the user at print time. Lines follow the customer order, each nut
    under its bolt and on its bolt's pallet (app/services/nut_pairing.py)."""
    from app.services.nut_pairing import line_order, pallets_by_line
    order = db.query(CustomerOrder).filter(CustomerOrder.id == shipment.order_id).first()
    designed = _designed(db, "packing_list", shipment, order.customer_id if order else None, include_boxes=include_boxes,
                         include_pallets=include_pallets, include_lots=include_lots, show_notes=show_notes,
                         include_pallet_boxes=include_pallet_boxes)
    if designed:
        return designed
    company = get_company_profile(db)
    customer = db.query(Customer).filter(Customer.id == order.customer_id).first() if order else None
    items = {i.id: i for i in db.query(StockItem).filter(
        StockItem.id.in_({l.item_id for l in shipment.lines} | {b.item_id for b in shipment.boxes})).all()}
    lots = {l.id: l for l in db.query(Lot).filter(Lot.id.in_({l.lot_id for l in shipment.lines if l.lot_id})).all()}

    story = _header(company, "PACKING LIST", shipment.code)  # customer-facing: no internal status label

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

    # Earlier shipments of the same order lines, so the customer can match a partial
    # delivery to what came before: "SH-0004: 200 · delivered Sep 03, 2026".
    previous = {}
    earlier = (db.query(ShipmentLine, Shipment).join(Shipment, Shipment.id == ShipmentLine.shipment_id)
               .filter(ShipmentLine.order_line_id.in_(list(shipped_by_line)), Shipment.id != shipment.id,
                       Shipment.status.in_(("shipped", "delivered", "invoiced")))
               .order_by(Shipment.ship_date, Shipment.id).all())
    for sl, sh in earlier:
        entry = previous.setdefault(sl.order_line_id, {}).setdefault(sh.id, {"code": sh.code, "qty": 0, "sh": sh})
        entry["qty"] += sl.quantity

    def previous_text(line_id):
        out = []
        for e in previous.get(line_id, {}).values():
            sh = e["sh"]
            when = (f"delivered {date(sh.delivered_at)}" if sh.delivered_at
                    else f"shipped {date(sh.ship_date)}" if sh.ship_date else "")
            out.append(f"{e['code']}: {qty(e['qty'])}" + (f" · {when}" if when else ""))
        return "\n".join(out)
    show_previous = bool(previous)

    codes = {i: it.code for i, it in items.items()}
    eff_pallets = pallets_by_line(shipment, codes)
    ordered = [ol.id for ol in line_order(list(order_lines.values()), lambda ol: codes.get(ol.item_id, ""))]
    rows, total_units, box_texts = [], 0, []
    for line_id in ordered:
        ol, shipped = order_lines[line_id], shipped_by_line[line_id]
        item = items.get(ol.item_id)
        backordered = max(0, ol.quantity - ol.shipped_quantity - ol.booked_quantity)
        by_qty = {}
        for b in shipment.boxes:
            if b.order_line_id == line_id:
                by_qty[b.quantity_in_box] = by_qty.get(b.quantity_in_box, 0) + 1
        boxes = "\n".join(f"{n} Box × {qty(q)}" for q, n in sorted(by_qty.items(), reverse=True)) or "—"
        line_pallets = ", ".join(eff_pallets.get(line_id, [])) or "—"
        row = [str(ol.line_no or ""), p(item.code if item else ol.item_id, "td"), described(item.title if item else "", ol, show_notes)]
        if include_lots:
            row.append(p(", ".join(dict.fromkeys(lots_by_line.get(line_id, []))) or "—", "td_muted"))
        if show_previous:
            row.append(p(previous_text(line_id) or "—", "td_muted"))
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
    headers = (["Line", "Item #", "Description"] + (["Lot #"] if include_lots else [])
               + (["Previously shipped"] if show_previous else []) + ["Ordered", "Shipped", "Backorder"])
    code_w = fit_width([i.code for i in items.values()], 0.85 * inch)
    widths = ([0.4 * inch, code_w, 0] + ([0.85 * inch] if include_lots else [])
              + ([1.75 * inch] if show_previous else []) + [0.62 * inch, 0.62 * inch, 0.72 * inch])
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
    for b in sorted(shipment.boxes, key=lambda b: (ordered.index(b.order_line_id) if b.order_line_id in ordered else 9999, b.box_number or 0)):
        key = b.pallet_number or (eff_pallets.get(b.order_line_id) or ["Unassigned"])[0]  # a nut rides on its bolt's pallet
        entry = pallets.setdefault(key, {"items": [], "boxes": 0})
        code = items[b.item_id].code if b.item_id in items else str(b.item_id)
        if code not in entry["items"]:
            entry["items"].append(code)
        entry["boxes"] += 1
    total_weight = sum(pw.weight or 0 for pw in shipment.pallets)
    pallet_count = len([k for k in pallets if k != "Unassigned"])

    cells = []
    if include_pallets and pallet_count:
        cells.append([p("PALLETS", "label"), p(str(pallet_count) if pallet_count else "—", "party")])
        cells.append([p("TOTAL WEIGHT", "label"), p(f"{total_weight:,.1f} lbs" if total_weight else "—", "party")])
    if cells:
        summary = Table([cells], colWidths=[7.3 * inch / len(cells)] * len(cells))
        summary.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT), ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
            ("LINEBEFORE", (1, 0), (-1, -1), 0.5, BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story += [Spacer(1, 14), summary]

    if include_pallets and pallet_count:
        from app.services.doc_context import _natural
        prow, po_no = [], (order.po_number if order and order.po_number else "—")
        for key, entry in sorted(pallets.items(), key=lambda kv: _natural(kv[0])):  # by pallet #: 1, 2, 10
            info = next((pw for pw in shipment.pallets if pw.pallet_number == key), None)
            if key == "Unassigned":
                continue
            prow.append([key, p(", ".join(entry["items"]), "td")] + ([str(entry["boxes"])] if include_pallet_boxes else [])
                        + [f"{info.weight:,.1f}" if info and info.weight else "—", p((info.dimensions if info else None) or "—", "td"), p(po_no, "td")])
        heads = ["Pallet #", "Items"] + (["Boxes"] if include_pallet_boxes else []) + ["Weight (lbs)", "Dimensions (L x W x H in)", "Customer PO #"]
        widths = [0.75 * inch, (3.05 if not include_pallet_boxes else 2.45) * inch] + ([0.6 * inch] if include_pallet_boxes else []) + [1.0 * inch, 1.4 * inch, 1.1 * inch]
        story += [Spacer(1, 16), p("PALLETS", "label"), Spacer(1, 4),
                  _data_table(heads, prow, widths, right_cols=(2, 3) if include_pallet_boxes else (2,))]

    story += _notes_box([("NOTES", shipment.notes)])

    trace = "  ·  ".join(x for x in [
        f"Packing list {shipment.code}",
        f"PO # {order.po_number}" if order and order.po_number else None,
        f"Order {order.code}" if order else None,
        f"Job {order.job_number}" if order and order.job_number else None,
    ] if x)
    return _build(story, _footer_text(company), f"Packing list {shipment.code}", trace_text=trace,
                  signature="Received by / date")


# ---- purchase order ----
def purchase_order_pdf(db: Session, po: PurchaseOrder, for_vendor: bool = False, show_notes: bool = True) -> bytes:
    """for_vendor=True is the copy that goes out: lines show only the vendor's part # and
    description -- our own item numbers stay internal. The internal copy shows both."""
    designed = _designed(db, "purchase_order", po, for_vendor=for_vendor, show_notes=show_notes)
    if designed:
        return designed
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
        from app.services.money import line_amount
        amount = line_amount(l.quantity, l.unit_cost)
        total += amount
        if for_vendor:
            rows.append([str(i), described(description, l, show_notes), qty(l.quantity), price(l.unit_cost), money(amount)])
        else:
            rows.append([str(i), p(item.code if item else l.item_id, "td"), p(l.vendor_item_code or "—", "td"),
                         described(description, l, show_notes), qty(l.quantity), price(l.unit_cost), money(amount)])
    # Freight / shipping / handling charges billed on top of the lines.
    for c in po.charges:
        label = c.charge_type.capitalize() + (f" — invoice {c.bill_number}" if c.bill_number else "") + (f" ({c.description})" if c.description and not c.bill_number else "")
        total += c.amount
        rows.append([""] + ([] if for_vendor else ["", ""]) + [p(label, "td"), "", "", money(c.amount)])
    if for_vendor:
        # No item # column on the vendor's copy -- description only.
        story.append(_data_table(["#", "Description", "Qty", "Unit price", "Amount"], rows,
                                 [0.35 * inch, 4.05 * inch, 0.8 * inch, 1.0 * inch, 1.1 * inch], right_cols=(2, 3, 4)))
    else:
        story.append(_data_table(["#", "Our item #", "Vendor item #", "Description", "Qty", "Unit cost", "Amount"], rows,
                                 [0.35 * inch, 1.3 * inch, 1.0 * inch, 1.8 * inch, 0.7 * inch, 1.0 * inch, 1.15 * inch],
                                 right_cols=(4, 5, 6)))

    totals = Table([[p("Total", "grand_k"), p(money(total), "grand_v")]], colWidths=[1.5 * inch, 1.4 * inch])
    totals.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HEAD_BG), ("BOX", (0, 0), (-1, -1), 1, NAVY),
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


# ---- quotation ----
def quote_pdf(db: Session, q) -> bytes:
    designed = _designed(db, "quote", q, q.customer_id)
    if designed:
        return designed
    from app.services.money import line_amount
    company = get_company_profile(db)
    customer = db.query(Customer).filter(Customer.id == q.customer_id).first()
    story = _header(company, "QUOTATION", q.code)
    to = _party_box("QUOTE FOR", customer.name if customer else "", [
        f"Attn: {customer.contact_name}" if customer and customer.contact_name else None,
        *address_lines(customer.address if customer else None),
        customer.phone if customer else None, customer.email if customer else None])
    meta = _meta_table([("Quote date", date(q.quote_date)), ("Valid until", date(q.valid_until)), ("Your reference", q.customer_ref)])
    story += [_two_boxes(to, meta), Spacer(1, 18)]
    items = {i.id: i for i in db.query(StockItem).filter(StockItem.id.in_({l.item_id for l in q.lines if l.item_id})).all()}
    rows, total = [], 0
    for n, l in enumerate(q.lines, 1):
        it = items.get(l.item_id)
        amt = line_amount(l.quantity, l.unit_price)
        total += amt
        rows.append([str(n), p(it.code if it else "", "td"), described(l.description or (it.title if it else ""), l),
                     qty(l.quantity), price(l.unit_price), money(amt)])
    code_w = fit_width([i.code for i in items.values()], 0.9 * inch)
    story.append(_data_table(["#", "Item #", "Description", "Qty", "Unit price", "Amount"], rows,
                             [0.35 * inch, code_w, 7.3 * inch - 0.35 * inch - code_w - 3.1 * inch, 0.9 * inch, 1.05 * inch, 1.15 * inch],
                             right_cols=(3, 4, 5)))
    totals = Table([[p("Quote total", "grand_k"), p(money(total), "grand_v")]], colWidths=[1.5 * inch, 1.4 * inch])
    totals.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HEAD_BG), ("BOX", (0, 0), (-1, -1), 1, NAVY), ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    totals.hAlign = "RIGHT"
    story += [Spacer(1, 10), KeepTogether(totals)]
    story += _notes_box([("NOTES", q.notes), ("TERMS", f"Prices valid until {date(q.valid_until)}." if q.valid_until else None)])
    story += [Spacer(1, 18), p("Thank you for the opportunity to quote.", "thanks")]
    return _build(story, _footer_text(company), f"Quote {q.code}")

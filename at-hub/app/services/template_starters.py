"""Ready-made templates the designer starts from: Classic (today's look), Executive (NetSuite feel),
Modern Bold (Xero feel) and Blank -- for every document -- plus box and address labels.
All sizes in inches; documents are Letter with 0.6 in margins (content 7.3 in wide)."""
import copy

INK, TEXT, MUTED, FAINT, RULE, PANEL = "#0f172a", "#1e293b", "#64748b", "#94a3b8", "#e2e8f0", "#f8fafc"
NAVY, BLUE, ACCENT = "#1b2a4a", "#2563eb", "#2f6fed"
W = 7.3
_n = [0]


def B(type_, x, y, w, h, text=None, value=None, **style):
    _n[0] += 1
    b = {"id": f"b{_n[0]}", "type": type_, "x": round(x, 3), "y": round(y, 3), "w": round(w, 3), "h": round(h, 3), "style": style}
    if text is not None:
        b["text"] = text
    if value is not None:
        b["value"] = value
    if type_ == "image":
        b["src"] = "logo"
    return b


def T(x, y, w, h, text, **style):
    style.setdefault("size", 9)
    style.setdefault("color", TEXT)
    return B("text", x, y, w, h, text, **style)


def LABEL(x, y, w, text, **kw):
    return T(x, y, w, 0.16, text, size=kw.pop("size", 6.8), bold=True, color=kw.pop("color", MUTED), upper=True, **kw)


# ---------- what each document says ----------
DOCS = {
    "invoice": {
        "facts": [("Invoice date", "{{invoice.date}}"), ("Due date", "{{invoice.due_date}}"), ("Terms", "{{invoice.terms}}"),
                  ("Customer PO", "{{order.po_number}}"), ("Amount due", "{{totals.total}}")],
        "parties": [("Bill to", "<b>{{customer.name}}</b>\nAttn: {{customer.contact}}\n{{customer.bill_to}}"),
                    ("Ship to", "<b>{{customer.name}}</b>\n{{customer.ship_to}}"),
                    ("Order details", "Order # <b>{{order.code}}</b>\nCustomer PO <b>{{order.po_number}}</b>\nJob # <b>{{order.job_number}}</b>\n"
                                      "Shipment <b>{{invoice.shipments}}</b>\nShipped <b>{{invoice.shipped}}</b>")],
        "hero": ("Amount due", "{{totals.total}}", "Due {{invoice.due_date}}"),
        "chips": [("Invoice date", "{{invoice.date}}"), ("Order #", "{{order.code}}"), ("Customer PO", "{{order.po_number}}"),
                  ("Job #", "{{order.job_number}}"), ("Shipment", "{{invoice.shipments}}"), ("Shipped", "{{invoice.shipped}}")],
        "columns": [("line_no", "#", 0.32), ("item_code_desc", "Item / description", 0), ("qty", "Qty", 0.9), ("price", "Unit price", 1.0), ("amount", "Amount", 1.15)],
        "totals": [("Subtotal", "{{totals.subtotal}}"), ("Tax", "{{totals.tax}}")], "due": ("Amount due", "{{totals.total}}"),
        "notes": [("Payment instructions & terms", "{{company.invoice_notes}}"), ("Notes", "{{invoice.notes}}\nThank you for your business!")],
    },
    "quote": {
        "facts": [("Quote date", "{{quote.date}}"), ("Valid until", "{{quote.valid_until}}"), ("Your reference", "{{quote.ref}}"),
                  ("Lines", "{{totals.lines}}"), ("Quote total", "{{totals.total}}")],
        "parties": [("Prepared for", "<b>{{customer.name}}</b>\nAttn: {{customer.contact}}\n{{customer.bill_to}}"),
                    ("Ship to", "<b>{{customer.name}}</b>\n{{customer.ship_to}}"),
                    ("Quote", "Quote # <b>{{doc.number}}</b>\nDate <b>{{quote.date}}</b>\nValid until <b>{{quote.valid_until}}</b>")],
        "hero": ("Quote total", "{{totals.total}}", "Valid until {{quote.valid_until}}"),
        "chips": [("Quote date", "{{quote.date}}"), ("Valid until", "{{quote.valid_until}}"), ("Your reference", "{{quote.ref}}"), ("Lines", "{{totals.lines}}")],
        "columns": [("line_no", "#", 0.32), ("item_code_desc", "Item / description", 0), ("qty", "Qty", 0.9), ("price", "Unit price", 1.0), ("amount", "Amount", 1.15)],
        "totals": [], "due": ("Quote total", "{{totals.total}}"),
        "notes": [("Notes", "{{quote.notes}}"), ("Terms", "Prices are valid until {{quote.valid_until}}. To order, reply with your PO.")],
    },
    "purchase_order": {
        "facts": [("PO date", "{{po.date}}"), ("Required by", "{{po.expected}}"), ("Vendor SO #", "{{po.vendor_so}}"),
                  ("Lines", "{{totals.lines}}"), ("PO total", "{{totals.total}}")],
        "parties": [("Vendor", "<b>{{vendor.name}}</b>\nAttn: {{vendor.contact}}\n{{vendor.address}}\n{{vendor.email}}"),
                    ("Ship to", "{{po.ship_to}}"),
                    ("Details", "PO date <b>{{po.date}}</b>\nRequired by <b>{{po.expected}}</b>\nVendor SO # <b>{{po.vendor_so}}</b>\nBuyer <b>{{po.buyer}}</b>")],
        "hero": ("PO total", "{{totals.total}}", "Required by {{po.expected}}"),
        "chips": [("PO date", "{{po.date}}"), ("Required by", "{{po.expected}}"), ("Vendor SO #", "{{po.vendor_so}}"), ("Lines", "{{totals.lines}}")],
        "columns": [("line_no", "#", 0.32), ("item_code_desc", "Your part # / description", 0), ("qty", "Qty", 0.9), ("price", "Unit cost", 1.0), ("amount", "Amount", 1.15)],
        "totals": [("Subtotal", "{{totals.subtotal}}"), ("Freight / charges", "{{totals.charges}}")], "due": ("PO total", "{{totals.total}}"),
        "notes": [("Instructions", "Please confirm receipt, pricing and ship date. Material test reports (MTRs) must ship with the material. "
                                   "Put our PO # on all boxes, packing lists and invoices."), ("Notes", "{{po.notes}}")],
        "sign": ["Authorized by"],
    },
    "packing_list": {
        "facts": [("Lines", "{{shipment.lines}}"), ("Units", "{{shipment.units}}"), ("Boxes", "{{shipment.boxes}}"),
                  ("Pallets", "{{shipment.pallets}}"), ("Weight", "{{shipment.weight}}")],
        "parties": [("Ship to", "<b>{{customer.name}}</b>\nAttn: {{customer.contact}}\n{{customer.ship_to}}\n{{customer.phone}}"),
                    ("Customer PO #", "<font size=17><b>{{order.po_number}}</b></font>"),
                    ("Shipment", "Order # <b>{{order.code}}</b>\nJob # <b>{{order.job_number}}</b>\nShip date <b>{{shipment.ship_date}}</b>\n"
                                 "Carrier <b>{{shipment.carrier}}</b>\nTracking # <b>{{shipment.tracking}}</b>")],
        "hero": ("Customer PO #", "{{order.po_number}}", "Order {{order.code}} · Job {{order.job_number}}"),
        "chips": [("Ship date", "{{shipment.ship_date}}"), ("Lines", "{{shipment.lines}}"), ("Units", "{{shipment.units}}"), ("Boxes", "{{shipment.boxes}}"),
                  ("Carrier", "{{shipment.carrier}}"), ("Tracking #", "{{shipment.tracking}}")],
        "columns": [("line_no", "Ln", 0.32), ("item_code_desc", "Item / description", 0), ("ordered", "Ordered", 0.72), ("shipped", "Shipped", 0.72),
                    ("backorder", "Backorder", 0.78), ("boxes", "Boxes", 1.1), ("check", "Check", 0.55)],
        "totals": None, "due": None,
        "notes": [("Notes", "{{shipment.notes}}"), ("Receiving", "Please count boxes against this list and note any shortage or damage "
                                                                 "on the carrier's delivery receipt before signing.")],
        "sign": ["Shipped by", "Carrier / driver", "Received by"], "qr": True, "barcode": "{{shipment.code}}",
    },
}


def _facts_strip(y, facts, highlight=True):
    out, n = [], len(facts)
    cw = W / n
    out.append(B("rect", 0, y, W, 0.62, bg=PANEL, border=0.6, border_color=RULE))
    for i, (k, v) in enumerate(facts):
        last = highlight and i == n - 1
        if last:
            out.append(B("rect", i * cw, y, cw, 0.62, bg="#eef2ff", border=0.6, border_color=RULE))
        elif i:
            out.append(B("line", i * cw, y, 0.01, 0.62, border=0.6, color=RULE))
        out.append(LABEL(i * cw + 0.12, y + 0.1, cw - 0.2, k))
        out.append(T(i * cw + 0.12, y + 0.27, cw - 0.2, 0.32, v, size=13 if last else 10, bold=True, color=NAVY if last else INK))
    return out


def _parties(y, parties, widths=(0.31, 0.31, 0.38), h=0.95):
    out, x = [], 0
    for (k, v), f in zip(parties, widths):
        out.append(LABEL(x, y, W * f - 0.15, k))
        out.append(T(x, y + 0.18, W * f - 0.15, h, v, size=8.8, lh=1.35))
        x += W * f
    return out


def _totals(doc, x=4.3, y=0.0, due_bg=NAVY):
    out, yy = [], y
    for k, v in doc["totals"] or []:
        out.append(T(x, yy, 1.55, 0.22, k, align="right", color=MUTED))
        out.append(T(x + 1.55, yy, 1.45, 0.22, v, align="right", bold=True))
        out.append(B("line", x, yy + 0.24, 3.0, 0.01, border=0.5, color=RULE))
        yy += 0.28
    if doc["due"]:
        out.append(B("rect", x, yy + 0.04, 3.0, 0.42, bg=due_bg))
        out.append(T(x + 0.1, yy + 0.12, 1.45, 0.3, doc["due"][0], align="right", bold=True, color="#ffffff", size=9.5))
        out.append(T(x + 1.55, yy + 0.08, 1.37, 0.32, doc["due"][1], align="right", bold=True, color="#ffffff", size=13))
        yy += 0.5
    return out, yy


def _notes(y, notes):
    out, n = [B("rect", 0, y, W, 0.78, bg=PANEL, border=0.6, border_color=RULE)], len(notes)
    for i, (k, v) in enumerate(notes):
        cw = W / n
        if i:
            out.append(B("line", i * cw, y, 0.01, 0.78, border=0.6, color=RULE))
        out.append(LABEL(i * cw + 0.12, y + 0.1, cw - 0.24, k))
        out.append(T(i * cw + 0.12, y + 0.28, cw - 0.24, 0.48, v, size=8.2, lh=1.35))
    return out


def _signs(y, labels):
    out, n = [], len(labels)
    cw = W / n
    for i, k in enumerate(labels):
        out.append(B("line", i * cw, y + 0.3, cw - 0.3, 0.01, border=0.8, color=TEXT))
        out.append(T(i * cw, y + 0.36, cw - 0.3, 0.2, k, size=8.2, color=MUTED))
        out.append(T(i * cw, y + 0.54, cw - 0.3, 0.16, "Name · Signature · Date", size=6.8, bold=True, color=MUTED, upper=True))
    return out


def _summary(doc, due_bg=NAVY):
    blocks, yy = _totals(doc, due_bg=due_bg) if doc["due"] else ([], 0)
    yy = yy + 0.2 if blocks else 0.1
    blocks += _notes(yy, doc["notes"])
    yy += 0.95
    if doc.get("sign"):
        blocks += _signs(yy, doc["sign"])
        yy += 0.85
    if doc.get("qr"):
        blocks.append(B("rect", 0, yy, W, 0.95, bg=PANEL, border=0.6, border_color=RULE))
        blocks.append(B("qr", 0.08, yy + 0.06, 0.83, 0.83, value="{{shipment.pod_url}}"))
        blocks.append(T(1.05, yy + 0.28, 6.0, 0.22, "Driver: scan to upload proof of delivery for {{shipment.code}}", bold=True))
        blocks.append(T(1.05, yy + 0.5, 6.0, 0.2, "{{shipment.pod_url}}", size=8, color=MUTED))
        yy += 1.05
    return {"h": round(yy, 2), "blocks": blocks}


def _footer(dark=False):
    return {"h": 0.35, "blocks": [
        B("line", 0, 0.02, W, 0.01, border=0.6, color=RULE),
        T(0, 0.1, 5.0, 0.2, "{{company.name}} · {{company.contact_line}}", size=7.5, color=FAINT),
        T(4.3, 0.1, 3.0, 0.2, "{{doc.title}} {{doc.number}} · Page {{page}} of {{pages}}", size=7.5, color=FAINT, align="right")]}


def _running():
    return {"h": 0.42, "blocks": [
        T(0, 0, 4.0, 0.2, "{{doc.title}} {{doc.number}}", size=8, bold=True, color=MUTED),
        T(3.3, 0, 4.0, 0.2, "{{company.name}}", size=8, bold=True, color=MUTED, align="right"),
        B("line", 0, 0.24, W, 0.01, border=0.6, color=RULE)]}


def _company_block(x, y, light=False):
    return [T(x, y, 4.2, 0.26, "{{company.name}}", size=12.5, bold=True, color="#ffffff" if light else INK),
            T(x, y + 0.28, 4.4, 0.5, "{{company.address}}\n{{company.contact_line}}", size=8, color="#c7d2fe" if light else MUTED, lh=1.3)]


def executive(doc_type):
    d = DOCS[doc_type]
    header = [B("image", 0, 0, 0.8, 0.8), *_company_block(0.95, 0.04),
              T(3.8, 0.0, 3.5, 0.2, "{{doc.title}}", size=8.5, bold=True, color=MUTED, align="right", spacing=3),
              T(3.3, 0.22, 4.0, 0.42, "{{doc.number}}", size=19, bold=True, color=INK, align="right"),
              B("line", 0, 0.9, W, 0.02, border=1.6, color=NAVY)]
    if d.get("barcode"):
        header.append(B("barcode", 5.1, 0.6, 2.2, 0.27, value=d["barcode"], show_text=False, align="right"))
    header += _facts_strip(1.05, d["facts"]) + _parties(1.85, d["parties"])
    return {"name": "Executive", "page": {"w": 8.5, "h": 11, "margin": 0.6}, "header": {"h": 2.95, "blocks": header}, "running": _running(),
            "table": {"columns": [{"key": k, "header": h, "w": w} for k, h, w in d["columns"]],
                      "style": {"header_bg": "#f1f5f9", "top_rule": NAVY, "header_color": MUTED, "row_rule": RULE, "size": 8.6}},
            "summary": _summary(d, NAVY), "footer": _footer()}


def modern(doc_type):
    d = DOCS[doc_type]
    hero_k, hero_v, hero_sub = d["hero"]
    header = [B("rect", -0.6, -0.6, 8.5, 1.3, bg=NAVY), B("rect", -0.6, 0.66, 8.5, 0.05, bg="#24365f"),
              B("rect", 0, -0.42, 0.88, 0.88, bg="#ffffff", radius=8), B("image", 0.07, -0.35, 0.74, 0.74),
              *_company_block(1.05, -0.36, light=True),
              T(3.8, -0.38, 3.5, 0.4, "{{doc.title}}", size=20, bold=True, color="#ffffff", align="right"),
              T(3.8, 0.02, 3.5, 0.25, "{{doc.number}}", size=10.5, bold=True, color="#c7d2fe", align="right")]
    if d.get("barcode"):
        header.append(B("barcode", 5.3, 0.76, 2.0, 0.26, value=d["barcode"], show_text=False, align="right"))
    party_k, party_v = d["parties"][0]
    header += [LABEL(0, 1.0, 3.5, party_k), T(0, 1.18, 3.6, 0.8, party_v, size=9, lh=1.35),
               T(3.8, 1.12, 3.5, 0.18, hero_k, size=8, bold=True, color=MUTED, align="right", upper=True),
               T(2.8, 1.3, 4.5, 0.48, hero_v, size=26, bold=True, color=INK, align="right"),
               T(3.3, 1.8, 4.0, 0.2, hero_sub, size=8.5, color=MUTED, align="right"),
               B("line", 0, 2.12, W, 0.01, border=0.6, color=RULE), B("line", 0, 2.72, W, 0.01, border=0.6, color=RULE)]
    cw = W / len(d["chips"])
    for i, (k, v) in enumerate(d["chips"]):
        header += [LABEL(i * cw + 0.08, 2.2, cw - 0.1, k), T(i * cw + 0.08, 2.37, cw - 0.1, 0.32, v, size=10, bold=True, color=INK)]
    return {"name": "Modern Bold", "page": {"w": 8.5, "h": 11, "margin": 0.6}, "header": {"h": 2.92, "blocks": header}, "running": _running(),
            "table": {"columns": [{"key": k, "header": h, "w": w} for k, h, w in d["columns"]],
                      "style": {"header_rule": NAVY, "header_rule_w": 1.6, "header_color": MUTED, "zebra": PANEL, "row_rule": RULE, "size": 8.6}},
            "summary": _summary(d, ACCENT), "footer": _footer()}


def classic(doc_type):
    d = DOCS[doc_type]
    meta = "\n".join(f"{k}  <b>{v}</b>" for k, v in d["chips"])
    party_k, party_v = d["parties"][0]
    header = [B("image", 0, 0, 0.85, 0.85),
              T(3.3, 0.0, 4.0, 0.5, "{{doc.title}}", size=26, bold=True, color=BLUE, align="right"),
              T(3.3, 0.5, 4.0, 0.22, "{{doc.number}}", size=11, bold=True, color=INK, align="right"),
              T(0, 0.95, 5.0, 0.28, "{{company.name}}", size=14, bold=True, color=INK),
              T(0, 1.24, 5.0, 0.48, "{{company.address}}\n{{company.contact_line}}", size=8.5, color=MUTED),
              B("line", 0, 1.78, W, 0.03, border=2.2, color=BLUE),
              B("rect", 0, 1.98, 3.9, 1.2, bg="#f2f5fa", border=0.6, border_color=RULE, radius=4),
              LABEL(0.15, 2.08, 3.6, party_k), T(0.15, 2.26, 3.6, 0.9, party_v, size=9.5, lh=1.35),
              T(4.2, 2.0, 3.1, 1.2, meta, size=8.8, align="right", lh=1.5)]
    return {"name": "Classic", "page": {"w": 8.5, "h": 11, "margin": 0.6}, "header": {"h": 3.35, "blocks": header}, "running": _running(),
            "table": {"columns": [{"key": k, "header": h, "w": w} for k, h, w in d["columns"]],
                      "style": {"header_bg": "#1e293b", "header_color": "#ffffff", "row_rule": RULE, "zebra": "#f8fafc", "size": 8.8}},
            "summary": _summary(d, BLUE), "footer": _footer()}


def blank(doc_type):
    d = DOCS[doc_type]
    return {"name": "Blank", "page": {"w": 8.5, "h": 11, "margin": 0.6},
            "header": {"h": 1.1, "blocks": [B("image", 0, 0, 0.7, 0.7), T(0.85, 0.05, 4, 0.3, "{{company.name}}", size=12, bold=True),
                                            T(3.3, 0.0, 4.0, 0.36, "{{doc.title}} {{doc.number}}", size=16, bold=True, align="right")]},
            "running": _running(), "table": {"columns": [{"key": k, "header": h, "w": w} for k, h, w in d["columns"]], "style": {"row_rule": RULE, "header_rule": INK}},
            "summary": {"h": 0.6, "blocks": _totals(d)[0] if d["due"] else []}, "footer": _footer()}


# ---------- labels (6 x 4 in, 0.15 in margin: 5.7 x 3.7 to design on) ----------
LW, LH = 5.7, 3.7


def classic_box_label():
    f = lambda x, k, v: [T(x, 0.82, 1.38, 0.15, k, size=7, bold=True, color=MUTED, upper=True), T(x, 0.98, 1.38, 0.3, v, size=12, bold=True, color=INK)]
    blocks = [B("rect", 0, 0, LW, LH, border=2, border_color="#000000"), B("image", 0.12, 0.1, 1.0, 0.55),
              T(1.25, 0.12, 4.35, 0.55, "{{label.customer}}", size=20, bold=True, color=INK, align="right", valign="middle")]
    for i, (k, v) in enumerate([("Shipment", "{{label.shipment}}"), ("Order #", "{{label.order}}"), ("PO #", "{{label.po}}"), ("Job #", "{{label.job}}")]):
        blocks += f(0.12 + i * 1.4, k, v)
    blocks += [B("line", 0.12, 1.36, 5.46, 0.01, border=1, color="#000000"),
               T(0.12, 1.45, 5.46, 0.8, "<b>ITEM # {{label.item_code}}</b>\n{{label.item_title}}", size=13, color=INK, lh=1.3),
               T(0.12, 2.3, 3, 0.18, "Quantity in box", size=7.5, bold=True, color=MUTED, upper=True),
               T(0.12, 2.48, 3.4, 0.62, "{{label.qty}}", size=34, bold=True, color=INK),
               T(3.6, 2.55, 1.98, 0.5, "Box {{label.box}} of {{label.boxes}}", size=13, bold=True, align="right", color=INK),
               B("line", 0.12, 3.17, 5.46, 0.01, border=1, color="#000000"),
               T(0.12, 3.24, 5.46, 0.38, "{{label.footer}}", size=8, color=TEXT, align="center")]
    return {"name": "Classic box label", "page": {"w": 6, "h": 4, "margin": 0.15}, "header": {"h": LH, "blocks": blocks}}


def po_box_label():
    blocks = [B("rect", 0, 0, LW, LH, border=2, border_color="#000000"),
              B("image", 0.12, 0.1, 0.9, 0.5), T(1.1, 0.12, 2.3, 0.5, "{{company.name}}", size=9, bold=True, valign="middle"),
              T(3.4, 0.08, 2.2, 0.16, "Ship to", size=7, bold=True, color=MUTED, upper=True, align="right"),
              T(2.9, 0.24, 2.7, 0.62, "<b>{{label.customer}}</b>\n{{label.ship_to}}", size=8, align="right", lh=1.2),
              B("rect", 0.12, 0.92, 5.46, 0.95, bg="#000000"),
              T(0.25, 0.98, 2.0, 0.18, "Customer PO #", size=8, bold=True, color="#ffffff", upper=True),
              T(0.25, 1.15, 5.2, 0.65, "{{label.po}}", size=38, bold=True, color="#ffffff"),
              T(0.12, 1.98, 3.5, 0.62, "<b>{{label.item_code}}</b>\n{{label.item_title}}", size=11, lh=1.25),
              T(3.7, 1.95, 1.88, 0.16, "Qty", size=7, bold=True, color=MUTED, upper=True, align="right"),
              T(3.7, 2.1, 1.88, 0.5, "{{label.qty}}", size=28, bold=True, align="right"),
              B("barcode", 0.12, 2.72, 3.4, 0.62, value="{{label.po}}", show_text=True),
              T(3.7, 2.75, 1.88, 0.55, "Box {{label.box}} of {{label.boxes}}\nOrder {{label.order}} · Job {{label.job}}", size=9, align="right", bold=True, lh=1.3),
              T(0.12, 3.42, 5.46, 0.2, "{{label.shipment}} · {{label.footer}}", size=7, color=MUTED, align="center")]
    return {"name": "Big PO + barcode", "page": {"w": 6, "h": 4, "margin": 0.15}, "header": {"h": LH, "blocks": blocks}}


def blank_label(doc_type):
    if doc_type == "address_label":
        return {"name": "Blank", "page": {"w": 6, "h": 4, "margin": 0.15}, "header": {"h": LH, "blocks": [T(0.2, 0.2, 5.3, 0.4, "{{label.to}}", size=14)]}}
    return {"name": "Blank", "page": {"w": 6, "h": 4, "margin": 0.15},
            "header": {"h": LH, "blocks": [B("rect", 0, 0, LW, LH, border=2, border_color="#000000"), T(0.2, 0.2, 5.3, 0.5, "{{label.item_code}}", size=20, bold=True)]}}


def classic_address_label():
    blocks = [B("rect", 0, 0, LW, LH, border=2, border_color="#000000"), B("image", 0.12, 0.1, 1.0, 0.55),
              T(0.12, 0.72, 2.6, 0.16, "From", size=7, bold=True, color=MUTED, upper=True),
              T(0.12, 0.88, 2.6, 0.75, "{{label.from}}", size=8.5, lh=1.25),
              T(1.4, 1.55, 4.1, 0.18, "Ship to", size=8, bold=True, color=MUTED, upper=True),
              T(1.4, 1.75, 4.1, 0.25, "Attn: {{label.attn}}", size=11, bold=True),
              T(1.4, 2.0, 4.1, 1.1, "{{label.to}}", size=15, bold=True, lh=1.25),
              T(0.12, 3.3, 2.8, 0.3, "Ref {{label.ref}}", size=9, bold=True), T(2.9, 3.3, 2.68, 0.3, "{{label.note}}", size=9, align="right")]
    return {"name": "Classic address label", "page": {"w": 6, "h": 4, "margin": 0.15}, "header": {"h": LH, "blocks": blocks}}


def starters(doc_type):
    """[(key, spec)] -- fresh copies every call."""
    if doc_type == "box_label":
        out = [("classic", classic_box_label()), ("big_po", po_box_label()), ("blank", blank_label(doc_type))]
    elif doc_type == "address_label":
        out = [("classic", classic_address_label()), ("blank", blank_label(doc_type))]
    else:
        out = [("classic", classic(doc_type)), ("executive", executive(doc_type)), ("modern", modern(doc_type)), ("blank", blank(doc_type))]
    return [(k, copy.deepcopy(s)) for k, s in out]

"""Ready-made templates the designer starts from: Classic (today's look), Executive (NetSuite feel),
Modern Bold (Xero feel), Minimal, Compact, Ledger, Coastal, Centered, Charcoal, Statement and Blank -- for every
document -- plus box and address labels. Every block sits in a show / hide section (g()) so the designer's
checklist can leave parts out.
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


def g(name, blocks):
    """Put blocks in a section of the designer's show / hide checklist ("Totals: Tax" is a part of "Totals")."""
    blocks = [blocks] if isinstance(blocks, dict) else blocks
    for b in blocks:
        b.setdefault("group", name)
    return blocks


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
        out += g(f"Totals: {k}", [T(x, yy, 1.55, 0.22, k, align="right", color=MUTED), T(x + 1.55, yy, 1.45, 0.22, v, align="right", bold=True),
                                  B("line", x, yy + 0.24, 3.0, 0.01, border=0.5, color=RULE)])
        yy += 0.28
    if doc["due"]:
        out += g(f"Totals: {doc['due'][0]}", [B("rect", x, yy + 0.04, 3.0, 0.42, bg=due_bg),
                  T(x + 0.1, yy + 0.12, 1.45, 0.3, doc["due"][0], align="right", bold=True, color="#ffffff", size=9.5),
                  T(x + 1.55, yy + 0.08, 1.37, 0.32, doc["due"][1], align="right", bold=True, color="#ffffff", size=13)])
        yy += 0.5
    return out, yy


def _notes(y, notes):
    out, n = g("Notes", [B("rect", 0, y, W, 0.78, bg=PANEL, border=0.6, border_color=RULE)]), len(notes)
    for i, (k, v) in enumerate(notes):
        cw = W / n
        if i:
            out += g("Notes", B("line", i * cw, y, 0.01, 0.78, border=0.6, color=RULE))
        out += g(f"Notes: {k}", [LABEL(i * cw + 0.12, y + 0.1, cw - 0.24, k), T(i * cw + 0.12, y + 0.28, cw - 0.24, 0.48, v, size=8.2, lh=1.35)])
    return out


def _signs(y, labels):
    out, n = [], len(labels)
    cw = W / n
    for i, k in enumerate(labels):
        out += g("Signatures", [B("line", i * cw, y + 0.3, cw - 0.3, 0.01, border=0.8, color=TEXT), T(i * cw, y + 0.36, cw - 0.3, 0.2, k, size=8.2, color=MUTED),
                                T(i * cw, y + 0.54, cw - 0.3, 0.16, "Name · Signature · Date", size=6.8, bold=True, color=MUTED, upper=True)])
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
        blocks += g("Proof-of-delivery QR", [B("rect", 0, yy, W, 0.95, bg=PANEL, border=0.6, border_color=RULE),
                                             B("qr", 0.08, yy + 0.06, 0.83, 0.83, value="{{shipment.pod_url}}"),
                                             T(1.05, yy + 0.28, 6.0, 0.22, "Driver: scan to upload proof of delivery for {{shipment.code}}", bold=True),
                                             T(1.05, yy + 0.5, 6.0, 0.2, "{{shipment.pod_url}}", size=8, color=MUTED)])
        yy += 1.05
    return {"h": round(yy, 2), "blocks": blocks}


def _footer(dark=False):
    return {"h": 0.35, "blocks": g("Footer", [
        B("line", 0, 0.02, W, 0.01, border=0.6, color=RULE),
        T(0, 0.1, 5.0, 0.2, "{{company.name}} · {{company.contact_line}}", size=7.5, color=FAINT),
        T(4.3, 0.1, 3.0, 0.2, "{{doc.title}} {{doc.number}} · Page {{page}} of {{pages}}", size=7.5, color=FAINT, align="right")])}


def _running():
    return {"h": 0.42, "blocks": g("Running header", [
        T(0, 0, 4.0, 0.2, "{{doc.title}} {{doc.number}}", size=8, bold=True, color=MUTED),
        T(3.3, 0, 4.0, 0.2, "{{company.name}}", size=8, bold=True, color=MUTED, align="right"),
        B("line", 0, 0.24, W, 0.01, border=0.6, color=RULE)])}


def _company_block(x, y, light=False):
    return [T(x, y, 4.2, 0.26, "{{company.name}}", size=12.5, bold=True, color="#ffffff" if light else INK),
            T(x, y + 0.28, 4.4, 0.5, "{{company.address}}\n{{company.contact_line}}", size=8, color="#c7d2fe" if light else MUTED, lh=1.3)]


def classic(doc_type):
    d = DOCS[doc_type]
    meta = "\n".join(f"{k}  <b>{v}</b>" for k, v in d["chips"])
    party_k, party_v = d["parties"][0]
    header = (g("Logo", B("image", 0, 0, 0.85, 0.85))
              + g("Title", [T(3.3, 0.0, 4.0, 0.5, "{{doc.title}}", size=26, bold=True, color=BLUE, align="right"),
                            T(3.3, 0.5, 4.0, 0.22, "{{doc.number}}", size=11, bold=True, color=INK, align="right")])
              + g("Company details", [T(0, 0.95, 5.0, 0.28, "{{company.name}}", size=14, bold=True, color=INK),
                                      T(0, 1.24, 5.0, 0.48, "{{company.address}}\n{{company.contact_line}}", size=8.5, color=MUTED)])
              + [B("line", 0, 1.78, W, 0.03, border=2.2, color=BLUE)]
              + g(party_k, [B("rect", 0, 1.98, 3.9, 1.2, bg="#f2f5fa", border=0.6, border_color=RULE, radius=4),
                            LABEL(0.15, 2.08, 3.6, party_k), T(0.15, 2.26, 3.6, 0.9, party_v, size=9.5, lh=1.35)])
              + g("Details", T(4.2, 2.0, 3.1, 1.2, meta, size=8.8, align="right", lh=1.5)))
    return {"name": "Classic", "page": {"w": 8.5, "h": 11, "margin": 0.6}, "header": {"h": 3.35, "blocks": header}, "running": _running(),
            "table": {"columns": [{"key": k, "header": h, "w": w} for k, h, w in d["columns"]],
                      "style": {"header_bg": "#1e293b", "header_color": "#ffffff", "row_rule": RULE, "zebra": "#f8fafc", "size": 8.8}},
            "summary": _summary(d, BLUE), "footer": _footer()}


def blank(doc_type):
    d = DOCS[doc_type]
    return {"name": "Blank", "page": {"w": 8.5, "h": 11, "margin": 0.6},
            "header": {"h": 1.1, "blocks": g("Logo", B("image", 0, 0, 0.7, 0.7)) + g("Company details", T(0.85, 0.05, 4, 0.3, "{{company.name}}", size=12, bold=True))
                                           + g("Title", T(3.3, 0.0, 4.0, 0.36, "{{doc.title}} {{doc.number}}", size=16, bold=True, align="right"))},
            "running": _running(), "table": {"columns": [{"key": k, "header": h, "w": w} for k, h, w in d["columns"]], "style": {"row_rule": RULE, "header_rule": INK}},
            "summary": {"h": 0.6, "blocks": _totals(d)[0] if d["due"] else []}, "footer": _footer()}


# ---------- labels (6 x 4 in, 0.15 in margin: 5.7 x 3.7 to design on) ----------
LW, LH = 5.7, 3.7


def classic_box_label():
    f = lambda x, k, v: g(f"Order info: {k}", [T(x, 0.82, 1.38, 0.15, k, size=7, bold=True, color=MUTED, upper=True), T(x, 0.98, 1.38, 0.3, v, size=12, bold=True, color=INK)])
    blocks = ([B("rect", 0, 0, LW, LH, border=2, border_color="#000000")] + g("Logo", B("image", 0.12, 0.1, 1.0, 0.55))
              + g("Customer", T(1.25, 0.12, 4.35, 0.55, "{{label.customer}}", size=20, bold=True, color=INK, align="right", valign="middle")))
    for i, (k, v) in enumerate([("Shipment", "{{label.shipment}}"), ("Order #", "{{label.order}}"), ("PO #", "{{label.po}}"), ("Job #", "{{label.job}}")]):
        blocks += f(0.12 + i * 1.4, k, v)
    blocks += ([B("line", 0.12, 1.36, 5.46, 0.01, border=1, color="#000000")]
               + g("Item", T(0.12, 1.45, 5.46, 0.8, "<b>ITEM # {{label.item_code}}</b>\n{{label.item_title}}", size=13, color=INK, lh=1.3))
               + g("Quantity", [T(0.12, 2.3, 3, 0.18, "Quantity in box", size=7.5, bold=True, color=MUTED, upper=True),
                                T(0.12, 2.48, 3.4, 0.62, "{{label.qty}}", size=34, bold=True, color=INK)])
               + g("Box count", T(3.6, 2.55, 1.98, 0.5, "Box {{label.box}} of {{label.boxes}}", size=13, bold=True, align="right", color=INK))
               + [B("line", 0.12, 3.17, 5.46, 0.01, border=1, color="#000000")]
               + g("Footer", T(0.12, 3.24, 5.46, 0.38, "{{label.footer}}", size=8, color=TEXT, align="center")))
    return {"name": "Classic box label", "page": {"w": 6, "h": 4, "margin": 0.15}, "header": {"h": LH, "blocks": blocks}}


def po_box_label():
    blocks = ([B("rect", 0, 0, LW, LH, border=2, border_color="#000000")]
              + g("Logo", B("image", 0.12, 0.1, 0.9, 0.5)) + g("Company name", T(1.1, 0.12, 2.3, 0.5, "{{company.name}}", size=9, bold=True, valign="middle"))
              + g("Ship to", [T(3.4, 0.08, 2.2, 0.16, "Ship to", size=7, bold=True, color=MUTED, upper=True, align="right"),
                              T(2.9, 0.24, 2.7, 0.62, "<b>{{label.customer}}</b>\n{{label.ship_to}}", size=8, align="right", lh=1.2)])
              + g("Customer PO #", [B("rect", 0.12, 0.92, 5.46, 0.95, bg="#000000"),
                                    T(0.25, 0.98, 2.0, 0.18, "Customer PO #", size=8, bold=True, color="#ffffff", upper=True),
                                    T(0.25, 1.15, 5.2, 0.65, "{{label.po}}", size=38, bold=True, color="#ffffff")])
              + g("Item", T(0.12, 1.98, 3.5, 0.62, "<b>{{label.item_code}}</b>\n{{label.item_title}}", size=11, lh=1.25))
              + g("Quantity", [T(3.7, 1.95, 1.88, 0.16, "Qty", size=7, bold=True, color=MUTED, upper=True, align="right"),
                               T(3.7, 2.1, 1.88, 0.5, "{{label.qty}}", size=28, bold=True, align="right")])
              + g("Barcode", B("barcode", 0.12, 2.72, 3.4, 0.62, value="{{label.po}}", show_text=True))
              + g("Box count", T(3.7, 2.75, 1.88, 0.55, "Box {{label.box}} of {{label.boxes}}\nOrder {{label.order}} · Job {{label.job}}", size=9, align="right", bold=True, lh=1.3))
              + g("Footer", T(0.12, 3.42, 5.46, 0.2, "{{label.shipment}} · {{label.footer}}", size=7, color=MUTED, align="center")))
    return {"name": "Big PO + barcode", "page": {"w": 6, "h": 4, "margin": 0.15}, "header": {"h": LH, "blocks": blocks}}


def blank_label(doc_type):
    if doc_type == "address_label":
        return {"name": "Blank", "page": {"w": 6, "h": 4, "margin": 0.15}, "header": {"h": LH, "blocks": [T(0.2, 0.2, 5.3, 0.4, "{{label.to}}", size=14)]}}
    if doc_type == "pallet_label":
        return {"name": "Blank", "page": {"w": 6, "h": 4, "margin": 0.15},
                "header": {"h": LH, "blocks": [B("rect", 0, 0, LW, LH, border=2, border_color="#000000"),
                                               T(0.2, 0.15, 5.3, 0.5, "PO # {{label.po}}", size=22, bold=True)] + _pallet_table(0.8, 2.75)}}
    return {"name": "Blank", "page": {"w": 6, "h": 4, "margin": 0.15},
            "header": {"h": LH, "blocks": [B("rect", 0, 0, LW, LH, border=2, border_color="#000000"), T(0.2, 0.2, 5.3, 0.5, "{{label.item_code}}", size=20, bold=True)]}}


def classic_address_label():
    blocks = ([B("rect", 0, 0, LW, LH, border=2, border_color="#000000")] + g("Logo", B("image", 0.12, 0.1, 1.0, 0.55))
              + g("From", [T(0.12, 0.72, 2.6, 0.16, "From", size=7, bold=True, color=MUTED, upper=True), T(0.12, 0.88, 2.6, 0.75, "{{label.from}}", size=8.5, lh=1.25)])
              + g("Ship to", [T(1.4, 1.55, 4.1, 0.18, "Ship to", size=8, bold=True, color=MUTED, upper=True),
                              T(1.4, 1.75, 4.1, 0.25, "Attn: {{label.attn}}", size=11, bold=True), T(1.4, 2.0, 4.1, 1.1, "{{label.to}}", size=15, bold=True, lh=1.25)])
              + g("Reference", T(0.12, 3.3, 2.8, 0.3, "Ref {{label.ref}}", size=9, bold=True)) + g("Note", T(2.9, 3.3, 2.68, 0.3, "{{label.note}}", size=9, align="right")))
    return {"name": "Classic address label", "page": {"w": 6, "h": 4, "margin": 0.15}, "header": {"h": LH, "blocks": blocks}}


# ======================================================================================================
# Executive and Modern Bold: the design samples shown earlier (pdf-samples/A-executive-*, B-modern-*),
# rebuilt block for block -- same font (Segoe UI), sizes, colours and spacing.
# Band coordinates are inches from the top-left of the content area (page margin 0.6 in).
# ======================================================================================================
SEMI_MUTED = dict(semi=True, color=MUTED)
TOP = 1.02     # A: where the content starts under the header rule
TOP_B = 0.95   # B: under the navy band


def _L(x, y, w, text):                      # small caps label (sample "label": semibold 7 pt, muted)
    return T(x, y, w, 0.14, text, size=7, upper=True, **SEMI_MUTED)


def _party(x, y, w, label, name, body):     # BILL TO / Hudson Products / address lines
    return g(label, [_L(x, y, w, label), T(x, y + 0.167, w, 0.21, name, size=10.5, bold=True, color=INK),
                     T(x, y + 0.354, w, 0.85, body, size=8.8, lh=1.364)])


def _kv(x, y, w, label, rows, empty=None, h=1.1):
    st = dict(size=8.8, label_size=8, label_w=0.95, lh=1.7, color=TEXT)
    if empty:
        st["empty"] = empty
    return g(label, [_L(x, y, w, label), B("kv", x, y + 0.181, w, h, "\n".join(f"{k} | {v}" for k, v in rows), **st)])


def _strip(y, facts, filled=True, highlight=False, bg=PANEL, rule=RULE, hi_bg="#eef2ff", hi_color=NAVY):
    """The key-facts strip: A = light panel with dividers (last cell highlighted), B = lines above and below."""
    n, out = len(facts), []
    cw, h = W / n, 0.535 if not highlight else 0.576
    if filled:
        out.append(B("rect", 0, y, W, h, bg=bg, border=0.6, border_color=rule))
    else:
        out += [B("line", 0, y, W, 0.01, border=0.6, color=rule), B("line", 0, y + h, W, 0.01, border=0.6, color=rule)]
    for i, (k, v) in enumerate(facts):
        big = highlight and i == n - 1
        cell = []
        if big:
            cell.append(B("rect", i * cw, y, cw, h, bg=hi_bg, border=0.6, border_color=rule))
        elif i and filled:
            out.append(B("line", i * cw, y, 0.01, h, border=0.6, color=rule))
        cell.append(T(i * cw + 0.139, y + 0.111, cw - 0.2, 0.13, k, size=6.8, upper=True, **SEMI_MUTED))
        cell.append(T(i * cw + 0.139, y + 0.229, cw - 0.2, 0.3, v, size=13 if big else 10,
                      **(dict(bold=True, color=hi_color) if big else dict(semi=True, color=INK))))
        out += g(f"Key facts: {k}", cell)
    return g("Key facts", out)


def _furniture_a():
    return (g("Logo", B("image", 0, -0.15, 0.75, 0.75))
            + g("Company details", [T(0.93, -0.12, 4.4, 0.22, "{{company.name}}", size=12.5, bold=True, color=INK),
                                    T(0.93, 0.115, 4.4, 0.62, "{{company.address}}\n{{company.phone}} · {{company.email}}\n{{company.website}}", size=8, color=MUTED, lh=1.3125)])
            + g("Title", [T(3.3, -0.1, 4.0, 0.16, "{{doc.title}}", size=8.5, align="right", spacing=4.6, **SEMI_MUTED),
                          T(3.0, 0.055, 4.3, 0.34, "{{doc.number}}", size=19, bold=True, color=INK, align="right")])
            + [B("line", 0, 0.76, W, 0.02, border=1.6, color=NAVY)])


def _furniture_b():
    return ([B("rect", -0.6, -0.6, 8.5, 1.25, bg=NAVY), B("rect", -0.6, 0.59, 8.5, 0.06, bg="#24365f")]
            + g("Logo", [B("rect", 0, -0.4, 0.85, 0.85, bg="#ffffff", radius=8), B("image", 0.08, -0.32, 0.69, 0.69, align="center")])
            + g("Company details", [T(1.02, -0.24, 4.2, 0.26, "{{company.name}}", size=14, bold=True, color="#ffffff"),
                                    T(1.02, 0.04, 4.6, 0.36, "{{company.address_line}} · {{company.phone}} · {{company.email}}\n{{company.website}}",
                                      size=8.2, color="#c7d2fe", lh=1.49)])
            + g("Title", [T(3.3, -0.28, 4.0, 0.34, "{{doc.title}}", size=20, bold=True, color="#ffffff", align="right"),
                          T(3.3, 0.1, 4.0, 0.2, "{{doc.number}}", size=10.5, semi=True, color="#c7d2fe", align="right")]))


def _hero(y, label, value, sub):
    return g(label, [T(3.65, y, 3.65, 0.14, label, size=8, align="right", upper=True, **SEMI_MUTED),
                     T(1.5, y + 0.167, 5.8, 0.42, value, size=26, bold=True, color=INK, align="right"),
                     T(3.65, y + 0.583, 3.65, 0.17, sub, size=8.8, color=MUTED, align="right")])


def _table(style_b, cols):
    st = {"header_upper": True, "header_size": 7, "header_color": MUTED, "row_rule": RULE, "size": 8.8, "pad": 6}
    st.update({"header_rule": NAVY, "header_rule_w": 1.6, "zebra": PANEL} if style_b else {"header_bg": "#f1f5f9", "top_rule": NAVY})
    return {"columns": [{"key": k, "header": h, "w": w, **({"align": a} if a else {})} for k, h, w, a in cols], "style": st}


def _totals_x(rows, due_label, due_value, y, due_bg):
    """Totals at the right; due_bg=None = no fill, a heavy rule above the amount instead."""
    out, yy = [], y
    for k, v in rows:
        out += g(f"Totals: {k}", [T(4.3, yy + 0.056, 1.44, 0.17, k, size=8.8, color=MUTED, align="right"),
                                  T(5.85, yy + 0.056, 1.34, 0.17, v, size=8.8, semi=True, color=TEXT, align="right"),
                                  B("line", 4.3, yy + 0.278, 3.0, 0.01, border=0.5, color=RULE)])
        yy += 0.278
    ink = "#ffffff" if due_bg else INK
    box = [B("rect", 4.3, yy, 3.0, 0.444, bg=due_bg)] if due_bg else [B("line", 4.3, yy + 0.02, 3.0, 0.02, border=1.4, color=INK)]
    out += g(f"Totals: {due_label}", box + [T(4.41, yy + 0.13, 1.33, 0.2, due_label, size=9.5, semi=True, color=ink, align="right"),
                                           T(5.85, yy + 0.09, 1.34, 0.28, due_value, size=13, bold=True, color=ink, align="right")])
    return out, yy + 0.444


def _panels(y, sections, h=0.75, bg=PANEL, rule=RULE, filled=True):
    n = len(sections)
    cw = W / n
    out = [B("rect", 0, y, W, h, bg=bg, border=0.6, border_color=rule)] if filled else [B("line", 0, y, W, 0.01, border=0.6, color=rule)]
    for i, (k, v) in enumerate(sections):
        if i and filled:
            out.append(B("line", i * cw, y, 0.01, h, border=0.6, color=rule))
        out += g(f"Notes: {k}", [_L(i * cw + 0.139, y + 0.111, cw - 0.28, k), T(i * cw + 0.139, y + 0.236, cw - 0.28, h - 0.27, v, size=8.2, lh=1.4)])
    return g("Notes", out)


def _signs(y, labels):
    n = len(labels)
    cw = W / n
    out = []
    for i, k in enumerate(labels):
        out += [B("line", i * cw, y, cw - 0.333, 0.01, border=0.8, color=TEXT),
                T(i * cw, y + 0.04, cw - 0.333, 0.16, k, size=8.2, color=MUTED),
                T(i * cw, y + 0.21, cw - 0.333, 0.14, "Name · Signature · Date", size=7, **SEMI_MUTED)]
    return g("Signatures", out)


def _qr_panel(y):
    return g("Proof-of-delivery QR", [B("rect", 0, y, W, 0.85, border=0.6, border_color=RULE), B("rect", 1.0, y, W - 1.0, 0.85, bg=PANEL),
            B("qr", 0.0, y, 0.85, 0.85, value="{{shipment.pod_url}}"),
            T(1.139, y + 0.29, 6.0, 0.18, "Driver: scan to upload proof of delivery for {{shipment.code}}", size=8.8, semi=True, color=TEXT),
            T(1.139, y + 0.47, 6.0, 0.16, "{{shipment.pod_url}}", size=8, color=MUTED)])


def _footer_ab(style_b):
    out = [B("rect", -0.6, 0.45, 8.5, 0.5, bg=PANEL)] if style_b else []
    return {"h": 0.35, "blocks": out + g("Footer", [
        B("line", 0, 0.45, W, 0.01, border=0.6, color=RULE),
        T(0, 0.53, 5.4, 0.14, "{{company.name}} · {{company.email}} · {{company.phone}} · {{company.website}}", size=7.5, color=FAINT),
        T(4.3, 0.53, 3.0, 0.14, "{{doc.name}} {{doc.number}}  ·  Page {{page}}", size=7.5, color=FAINT, align="right")])}


def _running_ab():
    return {"h": 0.15, "blocks": g("Running header", [
        T(0, -0.26, 4.0, 0.16, "{{doc.name}} {{doc.number}}", size=8, **SEMI_MUTED),
        T(3.3, -0.26, 4.0, 0.16, "{{company.name}}", size=8, align="right", **SEMI_MUTED),
        B("line", 0, -0.05, W, 0.01, border=0.6, color=RULE)])}


INV_COLS = [("line_no", "#", 0.32, ""), ("item_code_desc", "Item / description", 0, ""), ("qty", "Qty", 0.95, ""),
            ("price", "Unit price", 1.0, ""), ("amount", "Amount", 1.2, "")]
PO_COLS = [("line_no", "#", 0.32, ""), ("item_code_desc", "Your part # / description", 0, ""), ("qty", "Qty", 0.95, ""),
           ("price", "Unit cost", 1.0, ""), ("amount", "Amount", 1.2, "")]
PL_COLS = [("line_no", "Ln", 0.32, ""), ("item_code_desc", "Item / description", 0, ""), ("ordered", "Ordered", 0.72, ""),
           ("shipped", "Shipped", 0.72, ""), ("backorder", "Backorder", 0.78, ""), ("boxes", "Boxes", 1.18, "right"), ("check", "Check", 0.58, "center")]


def _sample(doc_type, b):
    """The sample design for one document: b = False -> Executive, True -> Modern Bold."""
    name = "Modern Bold" if b else "Executive"
    head = _furniture_b() if b else _furniture_a()
    y0 = TOP_B if b else TOP
    due_bg = ACCENT if b else NAVY
    page = {"w": 8.5, "h": 11, "margin": 0.6}
    if doc_type in ("invoice", "quote"):
        inv = doc_type == "invoice"
        bill = ("Bill to" if inv else "Prepared for", "{{customer.name}}", "Attn: {{customer.contact}}\n{{customer.bill_to}}")
        if b:
            head += _party(0, y0, 3.6, *bill)
            head += _hero(y0, "Amount due" if inv else "Quote total", "{{totals.total}}",
                          "Due {{invoice.due_date}}" if inv else "Valid until {{quote.valid_until}}")
            chips = ([("Invoice date", "{{invoice.date}}"), ("Order #", "{{order.code}}"), ("Customer PO", "{{order.po_number}}"),
                      ("Job #", "{{order.job_number}}"), ("Shipment", "{{invoice.shipments}}"), ("Shipped", "{{invoice.shipped}}")] if inv else
                     [("Quote date", "{{quote.date}}"), ("Valid until", "{{quote.valid_until}}"), ("Your reference", "{{quote.ref}}"), ("Lines", "{{totals.lines}}")])
            head += _strip(y0 + 1.048, chips, filled=False)
            h = y0 + 1.048 + 0.535 + 0.25
        else:
            facts = ([("Invoice date", "{{invoice.date}}"), ("Due date", "{{invoice.due_date}}"), ("Terms", "{{invoice.terms}}"),
                      ("Customer PO", "{{order.po_number}}"), ("Amount due", "{{totals.total}}")] if inv else
                     [("Quote date", "{{quote.date}}"), ("Valid until", "{{quote.valid_until}}"), ("Your reference", "{{quote.ref|—}}"),
                      ("Lines", "{{totals.lines}}"), ("Quote total", "{{totals.total}}")])
            head += _strip(y0, facts, highlight=True)
            py = y0 + 0.576 + 0.222
            head += _party(0, py, 2.07, *bill)
            head += _party(2.263, py, 2.07, "Ship to", "{{customer.name}}", "{{customer.ship_to}}")
            head += (_kv(4.526, py, 2.774, "Order details", [("Order #", "{{order.code}}"), ("Customer PO", "{{order.po_number}}"),
                     ("Job #", "{{order.job_number}}"), ("Shipment", "{{invoice.shipments}}"), ("Shipped", "{{invoice.shipped}}")]) if inv else
                     _kv(4.526, py, 2.774, "Quote", [("Quote #", "{{doc.number}}"), ("Date", "{{quote.date}}"), ("Valid until", "{{quote.valid_until}}"),
                                                     ("Your reference", "{{quote.ref}}")]))
            h = py + 1.22 + 0.25
        tot, yy = _totals_x([("Subtotal", "{{totals.subtotal}}"), ("Tax", "{{totals.tax}}")] if inv else [],
                            "Amount due" if inv else "Quote total", "{{totals.total}}", 0.167, due_bg)
        notes = ([("Payment instructions & terms", "{{company.invoice_notes}}"), ("Notes", "{{invoice.notes}}\nThank you for your business!")] if inv else
                 [("Notes", "{{quote.notes|—}}"), ("Terms", "Prices are valid until {{quote.valid_until}}. To order, reply with your PO.")])
        summary = {"h": round(yy + 0.222 + 0.8, 2), "blocks": tot + _panels(yy + 0.222, notes)}
        table = _table(b, INV_COLS)
    elif doc_type == "purchase_order":
        vendor = ("Vendor", "{{vendor.name}}", "Attn: {{vendor.contact}}\n{{vendor.address}}\n{{vendor.email}}")
        ship = ("Ship to", "{{company.name}}", "{{company.address}}\n{{company.phone}}")
        det = [("PO date", "{{po.date}}"), ("Required by", "{{po.expected}}"), ("Vendor SO #", "{{po.vendor_so}}"), ("Buyer", "{{po.buyer}}")]
        if b:
            head += _party(0, y0, 3.6, *vendor)
            head += _hero(y0, "PO total", "{{totals.total}}", "Required by {{po.expected}}")
            cy = y0 + 1.0 + 0.194
            head += _party(0, cy, 3.45, *ship) + _kv(3.65, cy, 3.65, "Details", det)
            h = cy + 1.04 + 0.22
        else:
            head += _strip(y0, [("PO date", "{{po.date}}"), ("Required by", "{{po.expected|ASAP}}"), ("Vendor SO #", "{{po.vendor_so|—}}"),
                                ("Lines", "{{totals.lines}}"), ("PO total", "{{totals.total}}")], highlight=True)
            py = y0 + 0.576 + 0.222
            head += _party(0, py, 2.29, *vendor) + _party(2.482, py, 2.22, *ship) + _kv(4.891, py, 2.409, "Details", det)
            h = py + 1.06 + 0.25
        tot, yy = _totals_x([("Subtotal", "{{totals.subtotal}}"), ("Freight / charges", "{{totals.charges}}")], "PO total", "{{totals.total}}", 0.167, due_bg)
        notes = [("Instructions", "Please confirm receipt, pricing and ship date. Material test reports (MTRs) must ship with the material. "
                                  "Reference our PO # on all boxes, packing lists and invoices."), ("Notes", "{{po.notes|—}}")]
        py = yy + 0.222
        summary = {"h": round(py + 0.8 + 0.6, 2), "blocks": tot + _panels(py, notes) + _signs(py + 0.8 + 0.36, ["Authorized by"])}
        table = _table(b, PO_COLS)
    else:  # packing list
        head += g("Barcode", B("barcode", 5.3, 0.77 if b else 0.44, 2.0, 0.27, value="{{shipment.code}}", show_text=False, align="right"))
        head += _party(0, y0, 3.6, "Ship to", "{{customer.name}}", "Attn: {{customer.contact}}\n{{customer.ship_to}}\n{{customer.phone}}")
        px = 0.52 * W
        head += g("Customer PO #", [_L(px, y0, 3.4, "Customer PO #"), T(px, y0 + 0.153, 3.4, 0.3, "{{order.po_number|—}}", size=17, bold=True, color=INK)])
        head += _kv(px, y0 + 0.542, 3.4, "Shipment", [("Order #", "{{order.code}}"), ("Job #", "{{order.job_number}}"), ("Ship date", "{{shipment.ship_date}}"),
                                                      ("Carrier", "{{shipment.carrier}}"), ("Tracking #", "{{shipment.tracking}}")], empty="—")
        ty = y0 + 1.764 + 0.194
        head += _strip(ty, [("Lines", "{{shipment.lines}}"), ("Units", "{{shipment.units}}"), ("Boxes", "{{shipment.boxes|—}}"),
                            ("Pallets", "{{shipment.pallets|—}}"), ("Weight", "{{shipment.weight|—}}")], filled=not b)
        h = ty + 0.535 + 0.222
        summary = {"h": 2.95, "blocks": _panels(0.222, [("Receiving", "<b>Notes:</b> {{shipment.notes}}\nPlease count boxes against this list and "
                                                       "note any shortage or damage on the carrier's delivery receipt before signing.")], h=0.6)
                   + _signs(0.222 + 0.6 + 0.25 + 0.36, ["Shipped by", "Carrier / driver", "Received by"]) + _qr_panel(0.222 + 0.6 + 0.25 + 0.36 + 0.42 + 0.194)}
        table = _table(b, PL_COLS)
    return {"name": name, "font": "ui", "page": page, "header": {"h": round(h, 2), "blocks": head}, "running": _running_ab(),
            "table": table, "summary": summary, "footer": _footer_ab(b)}


def executive(doc_type):
    return _sample(doc_type, False)


def modern(doc_type):
    return _sample(doc_type, True)


# ======================================================================================================
# More looks for every document (Minimal, Compact, Ledger, Coastal, Centered, Charcoal, Statement) and
# more labels. Same building blocks, every block in a show / hide section.
# ======================================================================================================
TEAL, TEAL_T, TEAL_R = "#0f766e", "#f0fdfa", "#99f6e4"
CHAR, ORANGE, ORANGE_T = "#1f2937", "#ea580c", "#fff7ed"


def _pt(x, y, w, k, v, h=0.95, size=8.8, color=MUTED):
    """A labelled address / details block (its own show / hide section)."""
    return g(k, [T(x, y, w, 0.14, k, size=7, upper=True, semi=True, color=color), T(x, y + 0.18, w, h, v, size=size, lh=1.36)])


def _three(y, d, color=MUTED, h=0.95, size=8.8):
    cw = W / 3
    return [b for i, (k, v) in enumerate(d["parties"]) for b in _pt(i * cw, y, cw - 0.2, k, v, h=h, size=size, color=color)]


def _tbl(d, **st):
    base = {"header_upper": True, "header_size": 7, "header_color": MUTED, "row_rule": RULE, "size": 8.8, "pad": 6}
    base.update(st)
    return {"columns": [{"key": k, "header": h, "w": w, **({"align": "center"} if k == "check" else {})} for k, h, w in d["columns"]], "style": base}


def _summary_n(d, due_bg, panel_bg=PANEL, rule=RULE, filled=True):
    blocks, yy = [], 0.222
    if d["due"]:
        blocks, yy = _totals_x(d["totals"] or [], d["due"][0], d["due"][1], 0.167, due_bg)
        yy += 0.222
    blocks += _panels(yy, d["notes"], bg=panel_bg, rule=rule, filled=filled)
    yy += 0.75 + 0.2
    if d.get("sign"):
        blocks += _signs(yy + 0.36, d["sign"])
        yy += 0.36 + 0.45
    if d.get("qr"):
        blocks += _qr_panel(yy + 0.1)
        yy += 0.1 + 0.85 + 0.1
    return {"h": round(yy, 2), "blocks": blocks}


def _doc(name, font, header_blocks, h, table, summary, footer, running):
    return {"name": name, "font": font, "page": {"w": 8.5, "h": 11, "margin": 0.6}, "header": {"h": round(h, 2), "blocks": header_blocks},
            "running": running, "table": table, "summary": summary, "footer": footer}


def minimal(doc_type):
    d = DOCS[doc_type]
    head = (g("Logo", B("image", 0, 0, 0.6, 0.6))
            + g("Company details", [T(0.75, 0.0, 3.6, 0.22, "{{company.name}}", size=11, bold=True, color=INK),
                                    T(0.75, 0.24, 3.6, 0.5, "{{company.address_line}}\n{{company.contact_line}}", size=7.8, color=MUTED, lh=1.35)])
            + g("Title", [T(3.3, 0.0, 4.0, 0.18, "{{doc.title}}", size=8.5, spacing=3, align="right", semi=True, color=FAINT),
                          T(3.0, 0.2, 4.3, 0.4, "{{doc.number}}", size=20, bold=True, color=INK, align="right")]))
    if d.get("barcode"):
        head += g("Barcode", B("barcode", 5.3, 0.66, 2.0, 0.24, value=d["barcode"], show_text=False, align="right"))
    head += _three(1.05, d)
    head += _strip(2.25, d["facts"], filled=False)
    return _doc("Minimal", "ui", head, 2.25 + 0.535 + 0.3, _tbl(d, header_rule=INK, header_rule_w=0.8, row_rule="#eef2f7"),
                _summary_n(d, None, filled=False), _footer_ab(False), _running_ab())


def compact(doc_type):
    d = DOCS[doc_type]
    head = (g("Logo", B("image", 0, 0, 0.5, 0.5))
            + g("Company details", [T(0.62, 0.0, 3.1, 0.2, "{{company.name}}", size=10.5, bold=True, color=INK),
                                    T(0.62, 0.21, 3.6, 0.3, "{{company.address_line}}\n{{company.contact_line}}", size=7.2, color=MUTED, lh=1.25)])
            + g("Title", [T(3.8, 0.0, 3.5, 0.28, "{{doc.title}}", size=15, bold=True, color=BLUE, align="right"),
                          T(3.8, 0.3, 3.5, 0.18, "{{doc.number}}", size=9.5, semi=True, color=INK, align="right")])
            + [B("line", 0, 0.6, W, 0.01, border=0.8, color=RULE)])
    if d.get("barcode"):
        head += g("Barcode", B("barcode", 3.85, 0.06, 1.4, 0.22, value=d["barcode"], show_text=False))
    y = 0.7
    head += _pt(0, y, 2.2, *d["parties"][0], h=0.85, size=8) + _pt(2.35, y, 2.2, *d["parties"][1], h=0.85, size=8)
    rows = d["chips"]
    head += g("Details", [T(4.75, y, 2.55, 0.14, "Details", size=7, upper=True, semi=True, color=MUTED),
                          B("kv", 4.75, y + 0.18, 2.55, len(rows) * 0.17 + 0.05, "\n".join(f"{k} | {v}" for k, v in rows),
                            size=8, label_size=7.2, label_w=0.95, lh=1.5, color=TEXT)])
    h = y + 0.18 + max(0.9, len(rows) * 0.17) + 0.15
    return _doc("Compact", "sans", head, h, _tbl(d, size=7.8, pad=3.5, header_bg="#e2e8f0", header_color=INK),
                _summary_n(d, BLUE), _footer(), _running())


def ledger(doc_type):
    d = DOCS[doc_type]
    head = ([B("rect", 0, 0, 4.35, 1.0, border=1, border_color=INK)]
            + g("Logo", B("image", 0.1, 0.1, 0.8, 0.8))
            + g("Company details", [T(1.0, 0.12, 3.25, 0.24, "{{company.name}}", size=12, bold=True, color=INK),
                                    T(1.0, 0.38, 3.25, 0.58, "{{company.address}}\n{{company.contact_line}}", size=7.8, color=MUTED, lh=1.3)])
            + g("Title", [B("rect", 4.45, 0, 2.85, 1.0, border=1, border_color=INK), B("rect", 4.45, 0, 2.85, 0.38, bg=INK),
                          T(4.45, 0.06, 2.85, 0.28, "{{doc.title}}", size=13, bold=True, color="#ffffff", align="center"),
                          T(4.45, 0.5, 2.85, 0.4, "{{doc.number}}", size=15, bold=True, color=INK, align="center")]))
    cw, y = (W - 0.2) / 3, 1.12
    for i, (k, v) in enumerate(d["parties"]):
        x = i * (cw + 0.1)
        head += g(k, [B("rect", x, y, cw, 1.15, border=0.8, border_color=INK), B("rect", x, y, cw, 0.24, bg="#e5e7eb", border=0.8, border_color=INK),
                      T(x + 0.08, y + 0.05, cw - 0.16, 0.16, k, size=7, bold=True, upper=True, color=INK),
                      T(x + 0.08, y + 0.32, cw - 0.16, 0.8, v, size=8.4, lh=1.32)])
    y2, fw = 2.39, W / len(d["facts"])
    for i, (k, v) in enumerate(d["facts"]):
        head += g(f"Key facts: {k}", [B("rect", i * fw, y2, fw, 0.55, border=0.8, border_color=INK),
                                      T(i * fw + 0.08, y2 + 0.07, fw - 0.16, 0.14, k, size=6.8, bold=True, upper=True, color=MUTED),
                                      T(i * fw + 0.08, y2 + 0.24, fw - 0.16, 0.28, v, size=10, bold=True, color=INK)])
    return _doc("Ledger", "sans", head, y2 + 0.55 + 0.2,
                _tbl(d, grid="#475569", row_rule="#475569", header_bg="#e5e7eb", header_color=INK, header_size=7.2, size=8.6, pad=5),
                _summary_n(d, INK, panel_bg="#ffffff", rule=INK), _footer(), _running())


def coastal(doc_type):
    d = DOCS[doc_type]
    lbl, val, sub = d["hero"]
    y, y2 = 1.85, 2.95
    h = y2 + 0.576 + 0.25
    head = ([B("rect", -0.6, -0.6, 0.2, h + 0.6, bg=TEAL)]
            + g("Logo", B("image", 0, 0, 0.8, 0.8))
            + g("Company details", [T(3.0, 0.0, 4.3, 0.24, "{{company.name}}", size=12, bold=True, color=INK, align="right"),
                                    T(3.0, 0.26, 4.3, 0.55, "{{company.address}}\n{{company.contact_line}}", size=8, color=MUTED, align="right", lh=1.3)])
            + g("Title", [T(0, 0.95, 4.0, 0.5, "{{doc.title}}", size=28, bold=True, color=TEAL),
                          T(0, 1.45, 4.0, 0.22, "{{doc.number}}", size=11, semi=True, color=INK)])
            + g(lbl, [T(3.8, 0.98, 3.5, 0.14, lbl, size=7.5, upper=True, semi=True, color=MUTED, align="right"),
                      T(3.3, 1.14, 4.0, 0.4, val, size=22, bold=True, color=TEAL, align="right"),
                      T(3.8, 1.52, 3.5, 0.17, sub, size=8.5, color=MUTED, align="right")]))
    head += _three(y, d, color=TEAL)
    head += _strip(y2, d["facts"], highlight=True, bg=TEAL_T, rule=TEAL_R, hi_bg="#ccfbf1", hi_color=TEAL)
    return _doc("Coastal", "ui", head, h, _tbl(d, header_bg=TEAL, header_color="#ffffff", zebra=TEAL_T, row_rule="#ccfbf1"),
                _summary_n(d, TEAL, panel_bg=TEAL_T, rule=TEAL_R), _footer_ab(False), _running_ab())


def centered(doc_type):
    d = DOCS[doc_type]
    head = (g("Logo", B("image", W / 2 - 0.5, 0, 1.0, 0.6, align="center"))
            + g("Company details", [T(0, 0.66, W, 0.24, "{{company.name}}", size=13, bold=True, color=INK, align="center"),
                                    T(0, 0.92, W, 0.2, "{{company.address_line}} · {{company.contact_line}}", size=7.8, color=MUTED, align="center")])
            + [B("line", 0, 1.2, W, 0.01, border=0.6, color=RULE)]
            + g("Title", [T(0, 1.3, W, 0.3, "{{doc.title}}", size=16, bold=True, color=INK, align="center", spacing=4),
                          T(0, 1.62, W, 0.2, "{{doc.number}}", size=10, semi=True, color=MUTED, align="center")]))
    head += _three(2.0, d)
    head += _strip(3.05, d["facts"], filled=False)
    return _doc("Centered", "ui", head, 3.05 + 0.535 + 0.25,
                _tbl(d, top_rule=INK, header_rule=INK, header_rule_w=0.8, zebra="#fafafa"), _summary_n(d, INK), _footer(), _running())


def charcoal(doc_type):
    d = DOCS[doc_type]
    head = ([B("rect", -0.6, -0.6, 8.5, 1.3, bg=CHAR), B("rect", -0.6, 0.7, 8.5, 0.06, bg=ORANGE)]
            + g("Logo", [B("rect", 0, -0.4, 0.85, 0.85, bg="#ffffff", radius=6), B("image", 0.08, -0.32, 0.69, 0.69, align="center")])
            + g("Company details", [T(1.02, -0.26, 4.2, 0.26, "{{company.name}}", size=14, bold=True, color="#ffffff"),
                                    T(1.02, 0.02, 4.4, 0.5, "{{company.address_line}}\n{{company.contact_line}}", size=8, color="#d1d5db", lh=1.4)])
            + g("Title", [T(3.3, -0.3, 4.0, 0.36, "{{doc.title}}", size=22, bold=True, color="#ffffff", align="right"),
                          T(3.3, 0.1, 4.0, 0.22, "{{doc.number}}", size=11, bold=True, color="#fdba74", align="right")]))
    head += _three(1.02, d, color=ORANGE)
    head += _strip(2.12, d["facts"], highlight=True, hi_bg=ORANGE_T, hi_color="#9a3412")  # deep orange: still dark in grey
    return _doc("Charcoal", "ui", head, 2.12 + 0.576 + 0.25, _tbl(d, header_bg=CHAR, header_color="#ffffff", zebra="#f9fafb"),
                _summary_n(d, ORANGE), _footer_ab(False), _running_ab())


def statement(doc_type):
    d = DOCS[doc_type]
    lbl, val, sub = d["hero"]
    facts = [f for f in d["facts"] if f[1] != val]
    px, pw = 4.55, 2.75
    head = (g("Logo", B("image", 0, 0, 0.7, 0.7))
            + g("Company details", [T(0.85, 0.0, 3.4, 0.24, "{{company.name}}", size=12.5, bold=True, color=INK),
                                    T(0.85, 0.26, 3.4, 0.55, "{{company.address}}\n{{company.contact_line}}", size=7.8, color=MUTED, lh=1.3)])
            + _pt(0, 1.0, 2.1, *d["parties"][0]) + _pt(2.2, 1.0, 2.1, *d["parties"][1]) + _pt(0, 2.15, 4.3, *d["parties"][2], h=0.85)
            + [B("rect", px, 0, pw, 3.0, bg="#eef2ff", radius=6)]
            + g("Title", [T(px + 0.2, 0.15, pw - 0.4, 0.3, "{{doc.title}}", size=16, bold=True, color=NAVY),
                          T(px + 0.2, 0.45, pw - 0.4, 0.2, "{{doc.number}}", size=10, semi=True, color=INK)])
            + g("Key facts", B("kv", px + 0.2, 0.8, pw - 0.4, 1.3, "\n".join(f"{k} | {v}" for k, v in facts), size=8.6, label_size=7.6, label_w=1.0, lh=1.65, color=TEXT))
            + g(lbl, [B("line", px + 0.2, 2.15, pw - 0.4, 0.01, border=0.6, color="#c7d2fe"),
                      T(px + 0.2, 2.25, pw - 0.4, 0.14, lbl, size=7, upper=True, semi=True, color=MUTED),
                      T(px + 0.2, 2.4, pw - 0.4, 0.38, val, size=20, bold=True, color=NAVY),
                      T(px + 0.2, 2.78, pw - 0.4, 0.16, sub, size=7.8, color=MUTED)]))
    return _doc("Statement", "ui", head, 3.25, _tbl(d, header_rule=NAVY, header_rule_w=1.6, zebra=PANEL), _summary_n(d, NAVY), _footer_ab(False), _running_ab())


MORE_DOCS = [("minimal", minimal), ("compact", compact), ("ledger", ledger), ("coastal", coastal), ("centered", centered),
             ("charcoal", charcoal), ("statement", statement)]


def big_item_box_label():
    blocks = ([B("rect", 0, 0, LW, LH, border=2, border_color="#000000")]
              + g("Item #", [T(0.12, 0.08, 5.46, 0.16, "Item #", size=7, bold=True, color=MUTED, upper=True),
                             T(0.12, 0.22, 5.46, 0.6, "{{label.item_code}}", size=30, bold=True, color=INK)])
              + g("Item description", T(0.12, 0.85, 5.46, 0.5, "{{label.item_title}}", size=11, lh=1.25))
              + [B("line", 0.12, 1.4, 5.46, 0.01, border=1, color="#000000")]
              + g("Quantity", [T(0.12, 1.48, 2.6, 0.16, "Qty in box", size=7, bold=True, color=MUTED, upper=True),
                               T(0.12, 1.62, 2.8, 0.7, "{{label.qty}}", size=40, bold=True, color=INK)])
              + g("Box count", T(3.0, 1.62, 2.58, 0.5, "Box {{label.box}} of {{label.boxes}}", size=16, bold=True, align="right"))
              + g("Lot #", T(3.0, 2.15, 2.58, 0.22, "Lot {{label.lot}}", size=9, bold=True, align="right"))
              + [B("line", 0.12, 2.45, 5.46, 0.01, border=1, color="#000000")]
              + g("Order info", T(0.12, 2.52, 3.4, 0.72, "<b>{{label.customer}}</b>\nPO <b>{{label.po}}</b>\nOrder {{label.order}} · Job {{label.job}}", size=8.5, lh=1.3))
              + g("Barcode", B("barcode", 3.6, 2.55, 1.98, 0.6, value="{{label.item_code}}", show_text=False, align="right"))
              + g("Footer", T(0.12, 3.35, 5.46, 0.25, "{{company.name}} · {{label.footer}}", size=7.5, color=MUTED, align="center")))
    return {"name": "Big item #", "page": {"w": 6, "h": 4, "margin": 0.15}, "header": {"h": LH, "blocks": blocks}}


def qr_box_label():
    blocks = ([B("rect", 0, 0, LW, LH, border=2, border_color="#000000")]
              + g("Logo", B("image", 0.12, 0.1, 0.9, 0.5))
              + g("Customer", T(1.15, 0.1, 4.43, 0.5, "{{label.customer}}", size=16, bold=True, align="right", valign="middle"))
              + [B("line", 0.12, 0.7, 5.46, 0.01, border=1, color="#000000")]
              + g("Order info", B("kv", 0.12, 0.8, 3.6, 1.55, "Customer PO | {{label.po}}\nOrder # | {{label.order}}\nJob # | {{label.job}}\n"
                                  "Shipment | {{label.shipment}}\nLot # | {{label.lot}}\nPallet # | {{label.pallet}}",
                                  size=10, label_size=8, label_w=1.05, lh=1.55, color=INK))
              + g("QR code", B("qr", 4.0, 0.82, 1.55, 1.55, value="{{label.shipment}}"))
              + [B("line", 0.12, 2.45, 5.46, 0.01, border=1, color="#000000")]
              + g("Item", T(0.12, 2.52, 3.6, 0.75, "<b>{{label.item_code}}</b>\n{{label.item_title}}", size=10, lh=1.25))
              + g("Quantity", T(3.8, 2.5, 1.78, 0.45, "{{label.qty}}", size=24, bold=True, align="right"))
              + g("Box count", T(3.8, 2.95, 1.78, 0.25, "Box {{label.box}} of {{label.boxes}}", size=10, bold=True, align="right"))
              + g("Footer", T(0.12, 3.38, 5.46, 0.22, "{{label.footer}}", size=7.5, color=MUTED, align="center")))
    return {"name": "QR + lot", "page": {"w": 6, "h": 4, "margin": 0.15}, "header": {"h": LH, "blocks": blocks}}


def big_address_label():
    blocks = ([B("rect", 0, 0, LW, LH, border=2, border_color="#000000")]
              + g("From", [B("rect", 0, 0, LW, 0.62, bg="#000000"), T(0.12, 0.06, 5.4, 0.52, "{{label.from}}", size=7, color="#ffffff", lh=1.15)])
              + g("Ship to", [T(0.2, 0.8, 5.3, 0.2, "Ship to", size=9, bold=True, color=MUTED, upper=True),
                              T(0.2, 1.02, 5.3, 0.3, "Attn: {{label.attn}}", size=12, bold=True),
                              T(0.2, 1.35, 5.3, 1.6, "{{label.to}}", size=20, bold=True, lh=1.2)])
              + [B("line", 0.12, 3.05, 5.46, 0.01, border=1, color="#000000")]
              + g("Reference", T(0.12, 3.15, 2.8, 0.4, "Ref {{label.ref}}", size=11, bold=True))
              + g("Note", T(2.9, 3.15, 2.68, 0.4, "{{label.note}}", size=10, align="right")))
    return {"name": "Big ship-to", "page": {"w": 6, "h": 4, "margin": 0.15}, "header": {"h": LH, "blocks": blocks}}


def barcode_address_label():
    blocks = ([B("rect", 0, 0, LW, LH, border=2, border_color="#000000")]
              + g("Logo", B("image", 0.12, 0.1, 0.9, 0.5))
              + g("From", T(1.15, 0.1, 4.43, 0.6, "{{label.from}}", size=7.5, align="right", lh=1.2, color=MUTED))
              + [B("line", 0.12, 0.75, 5.46, 0.01, border=1, color="#000000")]
              + g("Ship to", [T(0.12, 0.85, 5.4, 0.18, "Ship to", size=8, bold=True, color=MUTED, upper=True),
                              T(0.12, 1.05, 5.4, 0.25, "Attn: {{label.attn}}", size=11, bold=True),
                              T(0.12, 1.32, 5.4, 1.3, "{{label.to}}", size=16, bold=True, lh=1.22)])
              + g("Barcode", B("barcode", 0.12, 2.75, 3.2, 0.62, value="{{label.ref}}", show_text=True))
              + g("Note", T(3.5, 2.8, 2.08, 0.55, "{{label.note}}", size=9, align="right")))
    return {"name": "Ship-to + barcode", "page": {"w": 6, "h": 4, "margin": 0.15}, "header": {"h": LH, "blocks": blocks}}


# ======================================================================================================
# Portal: the old MRPeasy portal's packing list (frontend/public/packing-list.html) and invoice
# (invoicing.html, generateInvoiceDraftPdf) -- same layout, labels, sizes (px x 0.75 = pt) and colours.
# ======================================================================================================
def portal_packing_list():
    W = 7.9   # Letter, 0.3 in margins
    lab = lambda x, y, w, t: T(x, y, w, 0.14, t, size=7.5, bold=True, color="#666666", upper=True)
    val = lambda x, y, w, h, t, **k: T(x, y, w, h, t, size=9, color="#333333", lh=1.25, **({"semi": True} | k))
    cw = (W - 0.36) / 2
    lx, rx = 0.1, 0.1 + cw + 0.16
    header = (g("Logo", B("image", W - 1.25, 0.04, 1.25, 0.46, align="center"))
              + g("Title", [T(1.35, 0.0, W - 2.7, 0.22, "{{company.name}}", size=11.25, bold=True, color="#2c3e50", align="center"),
                            T(1.35, 0.23, W - 2.7, 0.26, "Packing list {{shipment.code}}", size=13.5, bold=True, color="#333333", align="center")])
              + [B("line", 0, 0.58, W, 0.02, border=1.5, color="#1f2d3a")]
              + g("Shipment info", [B("rect", 0, 0.7, W, 1.92, border=0.75, border_color="#e0e0e0", radius=3)])
              + g("Shipment info: Date", [lab(lx, 0.8, cw, "Date"), val(lx, 0.95, cw, 0.18, "{{doc.date}}")])
              + g("Shipment info: Delivery date", [lab(lx, 1.19, cw, "Delivery Date"), val(lx, 1.34, cw, 0.18, "{{order.delivery_date|N/A}}")])
              + g("Shipment info: Customer", [lab(lx, 1.58, cw, "Customer"), val(lx, 1.73, cw, 0.18, "{{customer.name|N/A}}")])
              + g("Shipment info: Shipping address", [lab(lx, 1.97, cw, "Shipping Address"), val(lx, 2.12, cw, 0.48, "{{customer.ship_to|N/A}}", semi=False)])
              + g("Shipment info: Reference", [lab(rx, 0.8, cw, "Reference"), val(rx, 0.95, cw, 0.18, "{{order.po_number|N/A}}")])
              + g("Shipment info: Job #", [lab(rx, 1.19, cw, "Job #"), val(rx, 1.34, cw, 0.18, "{{order.job_number|N/A}}")])
              + g("Shipment info: Supplier", [lab(rx, 1.58, cw, "Supplier"),
                                              val(rx, 1.73, cw, 0.8, "{{company.name}}\nPhone: {{company.phone}}\nWebsite: {{company.website}}\nE-mail: {{company.email}}", semi=False)]))
    grid = {"header_bg": "#e5e7eb", "header_color": "#000000", "header_size": 7.5, "header_upper": True, "grid": "#9aa4ad",
            "zebra": "#f8f9fa", "size": 8.25, "color": "#000000", "pad": 5, "row_rule": "#9aa4ad"}
    left = lambda k, h, w, **x: {"key": k, "header": h, "w": w, "align": "left", **x}
    return {"name": "Portal", "font": "gothic", "page": {"w": 8.5, "h": 11, "margin": 0.3},
            "header": {"h": 2.75, "blocks": header},
            "table": {"columns": [left("item_code", "Part #", 0.87, bold=True), left("description", "Part description", 0),
                                  left("ordered", "Qty ordered", 0.72), left("shipped", "Qty shipped", 0.72), left("backorder", "Backordered", 0.95, empty="0"),
                                  left("boxes", "Box breakdown", 1.0), left("pallet", "Pallet #", 0.7)], "style": grid},
            "pallets": {"heading": "Pallet Information", "new_page": True, "style": {**grid, "heading_size": 12},
                        "columns": [left("pallet", "Pallet #", 0.7), left("items", "Customer Item #", 0), left("weight", "Weight (lbs)", 0.85),
                                    left("dimensions", "Dimensions (in) L x W x H", 1.35), left("po", "Customer PO #", 1.15)]},
            "summary": {"h": 0.62, "blocks": g("Received by", [B("line", 0, 0.1, W, 0.01, border=0.75, color="#7f8b96"),
                                                                T(0, 0.18, W, 0.18, "Received by: __________________      Date: __________________", size=8.25, color="#666666", align="center")])
                                              + g("Thank you", T(0, 0.38, W, 0.18, "Thank You For Your Business.", size=8.25, bold=True, color="#333333", align="center"))},
            "footer": {"h": 0.22, "blocks": g("Page number", T(W - 1.5, 0.04, 1.5, 0.16, "Page {{page}}", size=6.75, color="#444444", align="right"))}}


def portal_invoice():
    W = 7.5   # Letter, 0.5 in margins
    meta = ("<b>Order:</b> {{order.code|N/A}}\n<b>PO #:</b> {{order.po_number|N/A}}\n<b>Job #:</b> {{order.job_number|N/A}}\n"
            "<b>Customer:</b> {{customer.name|N/A}}")
    header = (g("Title", T(0.15, 0.15, 2.5, 0.2, "Invoice", size=9.75, bold=True, color="#0f172a"))
              + g("Logo", B("image", W - 1.75 - 1.67 - 0.17, 0.15, 1.67, 0.6, align="center"))
              + g("Invoice #", T(W - 1.75 - 0.15, 0.15, 1.75, 0.16, "<b>Invoice #:</b> {{doc.number}}", size=6, color="#334155", align="right"))
              + [B("line", 0.15, 0.86, W - 0.3, 0.02, border=1.5, color="#e2e8f0")]
              + g("Order details", T(0.15, 0.97, W - 0.3, 0.5, meta, size=6, color="#334155", lh=1.45)))
    tot = lambda y, k, v, **x: [T(W - 0.15 - 2.92, y, 1.6, 0.16, k, size=x.get("size", 6.15), bold=x.get("bold", False), color="#0f172a"),
                                T(W - 0.15 - 1.32, y, 1.32, 0.16, v, size=x.get("size", 6.15), bold=x.get("bold", False), color="#0f172a", align="right")]
    summary = (g("Totals: Subtotal", tot(0.12, "Subtotal:", "{{totals.items_subtotal}}"))
               + g("Totals: Shipping", tot(0.27, "Shipping:", "{{totals.shipping}}"))
               + g("Totals: Total", [B("line", W - 0.15 - 2.92, 0.44, 2.92, 0.01, border=0.75, color="#94a3b8")]
                   + tot(0.5, "Total:", "{{totals.total}}", size=6.75, bold=True)))
    num = lambda k, h, w: {"key": k, "header": h, "w": w, "align": "right"}
    return {"name": "Portal", "font": "gothic", "page": {"w": 8.5, "h": 11, "margin": 0.5},
            "header": {"h": 1.55, "blocks": header},
            "table": {"omit_shipping": True,
                      "columns": [{"key": "item_code", "header": "Item Code", "w": 0.85}, {"key": "description", "header": "Description", "w": 0},
                                  {"key": "shipment", "header": "Shipment #", "w": 0.9}, {"key": "delivery", "header": "Delivery Date", "w": 0.9},
                                  num("qty", "Qty", 0.55), num("price", "Unit Price", 0.75), num("amount", "Line Total", 0.85)],
                      "style": {"header_bg": "#f8fafc", "header_color": "#0f172a", "header_size": 5.85, "header_upper": True, "grid": "#cbd5e1",
                                "zebra": "#f3f4f6", "size": 5.85, "color": "#0f172a", "pad": 3.5, "row_rule": "#cbd5e1", "bold_amount": False}},
            "summary": {"h": 0.72, "blocks": summary}}


# ---------- shipment pallet labels (4 x 6): PO # and job # big, then every pallet and the customer item #s on it ----------
PALLET_COLS = [{"key": "pallet", "header": "Pallet", "w": 0.72, "align": "center", "big": True},
               {"key": "items", "header": "Customer item #", "w": 0},
               {"key": "boxes", "header": "Boxes", "w": 0.6, "align": "center"}]


def _pallet_table(y, h, size=10.5):
    tb = B("table", 0.1, y, LW - 0.2, h, size=size, min_size=6, header_size=7, header_bg="#e5e7eb", pad=3, grid="#94a3b8", rule_color="#0f172a")
    tb["source"], tb["columns"] = "pallet_rows", copy.deepcopy(PALLET_COLS)
    return g("Pallet table", tb)


def _pallet_footer(y):
    return (g("Company", T(0.12, y, 2.9, 0.18, "{{label.footer_info}}", size=7, bold=True, color=MUTED))
            + g("Totals", T(2.9, y, 2.68, 0.18, "{{label.sheet}}   {{label.totals}}", size=7.5, bold=True,
                            color=INK, align="right")))


def classic_pallet_label():
    blocks = ([B("rect", 0, 0, LW, LH, border=2, border_color="#000000")]
              + g("Customer PO #", [T(0.14, 0.1, 3.3, 0.15, "Customer PO #", size=7, bold=True, color=MUTED, upper=True, spacing=0.6),
                                    T(0.14, 0.24, 3.38, 0.62, "{{label.po}}", size=34, bold=True, color=INK, valign="middle")])
              + [B("line", 3.6, 0.0, 0.01, 0.95, border=1.5, color="#000000")]
              + g("Job #", [T(3.74, 0.1, 1.85, 0.15, "Job #", size=7, bold=True, color=MUTED, upper=True, spacing=0.6),
                            T(3.74, 0.27, 1.88, 0.58, "{{label.job}}", size=22, bold=True, color=INK, valign="middle")])
              + [B("line", 0, 0.95, LW, 0.02, border=2, color="#000000")]
              + g("Customer", [T(0.14, 1.0, 2.75, 0.19, "{{label.customer}}", size=10.5, bold=True, color=INK),
                               T(0.14, 1.17, 2.75, 0.255, "{{label.ship_to}}", size=6, color=TEXT, lh=1.05)])
              + g("Shipment", [T(2.95, 1.0, 1.45, 0.14, "Shipment", size=6.5, bold=True, color=MUTED, upper=True),
                               T(2.95, 1.14, 1.45, 0.24, "{{label.shipment}}", size=9, bold=True, color=INK)])
              + g("Pallet badge", [B("rect", 4.45, 0.99, 1.15, 0.4, border=1.6, border_color="#000000", radius=5),
                                   T(4.45, 1.02, 1.15, 0.13, "{{label.badge_caption}}", size=6.5, bold=True, color=INK, align="center", upper=True, spacing=0.8),
                                   T(4.45, 1.14, 1.15, 0.24, "{{label.badge}}", size=13, bold=True, color=INK, align="center", upper=True)])
              + [B("line", 0, 1.43, LW, 0.01, border=1, color="#000000")]
              + _pallet_table(1.5, 1.94) + _pallet_footer(3.47))
    return {"name": "Classic pallet label", "page": {"w": 6, "h": 4, "margin": 0.15}, "header": {"h": LH, "blocks": blocks}}


def barcode_pallet_label():
    blocks = ([B("rect", 0, 0, LW, LH, border=2, border_color="#000000")]
              + g("Customer PO #", [T(0.14, 0.08, 2.6, 0.15, "Customer PO #", size=7, bold=True, color=MUTED, upper=True, spacing=0.6),
                                    T(0.14, 0.22, 3.2, 0.66, "{{label.po}}", size=40, bold=True, color=INK, valign="middle")])
              + g("PO barcode", B("barcode", 3.45, 0.12, 2.15, 0.62, value="{{label.po}}", show_text=False, align="right"))
              + g("Job #", T(3.45, 0.74, 2.15, 0.2, "JOB # {{label.job}}", size=11, bold=True, color=INK, align="right"))
              + [B("line", 0, 0.98, LW, 0.02, border=2, color="#000000")]
              + g("Customer", T(0.14, 1.04, 3.3, 0.22, "{{label.customer}} · {{label.shipment}}", size=9.5, bold=True, color=INK))
              + g("Pallet badge", T(3.5, 1.04, 2.08, 0.22, "{{label.badge_caption}} {{label.badge}}", size=11, bold=True, color=INK, align="right", upper=True))
              + _pallet_table(1.32, 2.12, size=11) + _pallet_footer(3.47))
    return {"name": "PO + barcode", "page": {"w": 6, "h": 4, "margin": 0.15}, "header": {"h": LH, "blocks": blocks}}


def starters(doc_type):
    """[(key, spec)] -- fresh copies every call."""
    if doc_type == "box_label":
        out = [("classic", classic_box_label()), ("big_po", po_box_label()), ("big_item", big_item_box_label()), ("qr_lot", qr_box_label()),
               ("blank", blank_label(doc_type))]
    elif doc_type == "pallet_label":
        out = [("classic", classic_pallet_label()), ("barcode", barcode_pallet_label()), ("blank", blank_label(doc_type))]
    elif doc_type == "address_label":
        out = [("classic", classic_address_label()), ("big_to", big_address_label()), ("barcode", barcode_address_label()), ("blank", blank_label(doc_type))]
    else:
        out = ([("classic", classic(doc_type)), ("executive", executive(doc_type)), ("modern", modern(doc_type))]
               + [(k, f(doc_type)) for k, f in MORE_DOCS] + [("blank", blank(doc_type))])
        if doc_type in ("packing_list", "invoice"):
            out.insert(3, ("portal", portal_packing_list() if doc_type == "packing_list" else portal_invoice()))
    from app.services.print_safe import make_safe  # no dark fills reach paper (black-and-white printers)
    return [(k, make_safe(copy.deepcopy(s))) for k, s in out]

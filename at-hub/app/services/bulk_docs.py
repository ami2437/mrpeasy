"""Bulk Operations -> Send / Print Documents: for many shipments (or invoices) at once, the packing lists, box labels,
invoices and proofs of delivery -- emailed to each customer (one email per order, or per shipment) or merged into
one PDF to print.

    plan(db, ...)     -> the emails it would send: recipient, subject, message, attachments, warnings (nothing sent)
    send(db, ...)     -> sends them (the screen may have edited recipient / subject / message per email)
    merged_pdf(db, ...) -> every chosen document of every chosen record in one PDF

Invoices that go out are logged and a draft becomes sent, exactly as when sent from the invoice screen; every email
is logged on its shipments too."""
import io
import json
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (Attachment, Customer, CustomerOrder, Invoice, InvoiceEmail, Shipment, ShipmentEmail,
                        StockItem)
from app.services.crud import InvoiceService, ShipmentService, get_company_profile
from app.services.pdf import invoice_pdf, packing_list_pdf

KINDS = {"packing_list": "Packing list", "labels": "Box labels", "invoice": "Invoice", "pod": "Proof of delivery"}


# ---------- documents ----------
def labels_pdf(db: Session, shipment: Shipment) -> Optional[bytes]:
    """The shipment's box labels as a PDF: the customer's / default box-label template, else the classic one.
    A shipment that left without saved packing (imported from MRPeasy) gets labels from today's pack sizes --
    nothing is saved on it."""
    from app.services import template_engine, template_starters
    from app.services.doc_context import label_context
    from app.services.templates import default_for
    boxes = list(shipment.boxes)
    if not boxes:
        if shipment.status in ShipmentService.OPEN_STATUSES:
            return None  # still being packed: accept the packing first
        boxes = ShipmentService.default_boxes(db, shipment)
    order = db.get(CustomerOrder, shipment.order_id)
    cust = db.get(Customer, order.customer_id) if order else None
    boxes = sorted(boxes, key=lambda b: (b.order_line_id or 0, b.box_number or 0)) if not shipment.boxes else sorted(boxes, key=lambda b: b.box_number or 0)
    labels = []
    for n, b in enumerate(boxes, 1):
        item = db.get(StockItem, b.item_id)
        labels.append({"customer": cust.name if cust else "", "shipment": shipment.code, "order": order.code if order else "",
                       "po": order.po_number if order else "", "job": order.job_number if order else "",
                       "item_code": item.code if item else "", "item_title": item.title if item else "", "qty": b.quantity_in_box,
                       "lot": b.lot_code or "", "pallet": b.pallet_number or "", "ship_to": order.ship_to_address if order else "",
                       "box": n, "boxes": len(boxes)})
    tpl = default_for(db, "box_label", cust.id if cust else None)
    spec = json.loads(tpl.spec) if tpl else template_starters.classic_box_label()
    return template_engine.render_labels(spec, [label_context(db, "box_label", l) for l in labels])


def pod_files(db: Session, shipment: Shipment) -> List[tuple]:
    from app.routes.attachments import upload_root
    out = []
    for a in db.query(Attachment).filter(Attachment.entity_type == "shipment", Attachment.entity_id == shipment.id,
                                         Attachment.category == "pod").order_by(Attachment.id).all():
        path = (upload_root() / a.stored_name).resolve()
        if path.exists():
            out.append((path.read_bytes(), a.filename, a.content_type or "application/octet-stream"))
    return out


# ---------- grouping ----------
def _invoice_of(db, sh):
    return InvoiceService.live_invoice_for_shipment(db, sh.id)


def plan(db: Session, shipment_ids: List[int], invoice_ids: List[int], kinds: List[str], group_by: str = "order",
         can_invoice: bool = True) -> List[dict]:
    kinds = [k for k in kinds if k in KINDS]
    if not kinds:
        raise HTTPException(status_code=400, detail="Pick at least one kind of document")
    if "invoice" in kinds and not can_invoice:
        raise HTTPException(status_code=403, detail="Your role doesn't include invoices")
    company = get_company_profile(db)
    groups: Dict[str, dict] = {}

    def group_for(key, order, cust):
        g = groups.get(key)
        if not g:
            g = groups[key] = {"key": key, "order_id": order.id if order else None, "order": order.code if order else "",
                               "po": order.po_number if order else "", "customer_id": cust.id if cust else None,
                               "customer": cust.name if cust else "", "shipments": [], "invoices": [], "attachments": [], "warnings": [],
                               "_cust": cust}
        return g

    for sid in dict.fromkeys(shipment_ids or []):
        sh = ShipmentService.get(db, sid)
        order = db.get(CustomerOrder, sh.order_id)
        cust = db.get(Customer, order.customer_id) if order else None
        g = group_for(f"order-{order.id}" if group_by == "order" and order else f"shipment-{sh.id}", order, cust)
        g["shipments"].append({"id": sh.id, "code": sh.code})
        if "packing_list" in kinds:
            g["attachments"].append({"kind": "packing_list", "name": f"Packing-List-{sh.code}.pdf"})
        if "labels" in kinds:
            if sh.boxes:
                g["attachments"].append({"kind": "labels", "name": f"Labels-{sh.code}.pdf", "detail": f"{len(sh.boxes)} labels"})
            elif sh.status in ShipmentService.OPEN_STATUSES:
                g["warnings"].append(f"{sh.code} isn't packed yet -- no labels")
            else:
                g["attachments"].append({"kind": "labels", "name": f"Labels-{sh.code}.pdf", "detail": "from current pack sizes"})
                g["warnings"].append(f"{sh.code} has no saved packing -- its labels use today's pack sizes")
        if "invoice" in kinds:
            inv = _invoice_of(db, sh)
            if inv and inv.id not in [i["id"] for i in g["invoices"]]:
                g["invoices"].append({"id": inv.id, "code": inv.code, "status": inv.status})
                g["attachments"].append({"kind": "invoice", "name": f"{inv.code}.pdf"})
            elif not inv:
                g["warnings"].append(f"{sh.code} has no invoice yet")
        if "pod" in kinds:
            files = db.query(Attachment).filter(Attachment.entity_type == "shipment", Attachment.entity_id == sh.id, Attachment.category == "pod").all()
            if files:
                g["attachments"] += [{"kind": "pod", "name": f.filename} for f in files]
            else:
                g["warnings"].append(f"{sh.code} has no proof of delivery")
    for iid in dict.fromkeys(invoice_ids or []):
        inv = InvoiceService.get(db, iid)
        if inv.status == "void":
            continue
        order = db.get(CustomerOrder, inv.order_id) if inv.order_id else None
        cust = db.get(Customer, inv.customer_id)
        g = group_for(f"invoice-{inv.id}", order, cust)
        g["invoices"].append({"id": inv.id, "code": inv.code, "status": inv.status})
        g["attachments"].append({"kind": "invoice", "name": f"{inv.code}.pdf"})
        for sh in inv.shipments:
            g["shipments"].append({"id": sh.id, "code": sh.code})
            if "packing_list" in kinds:
                g["attachments"].append({"kind": "packing_list", "name": f"Packing-List-{sh.code}.pdf"})

    out = []
    for g in groups.values():
        cust = g.pop("_cust")
        billing = bool(g["invoices"])
        g["to"] = ((cust.invoice_email if billing else None) or (cust.email if cust else None) or "") if cust else ""
        if not g["to"]:
            g["warnings"].append("No email address on the customer -- type one in")
        what = [KINDS[k].lower() + ("s" if k in ("packing_list",) and len(g["shipments"]) > 1 else "")
                for k in ("invoice", "packing_list", "labels", "pod") if any(a["kind"] == k for a in g["attachments"])]
        docs = ", ".join(what[:-1]) + (" and " if len(what) > 1 else "") + (what[-1] if what else "documents")
        ref = f"your PO {g['po']}" if g["po"] else (g["order"] or ", ".join(s["code"] for s in g["shipments"]))
        g["subject"] = f"{docs[:1].upper()}{docs[1:]} for {ref}" + (f" · {company.name}" if company.name else "")
        sh_codes = ", ".join(s["code"] for s in g["shipments"])
        g["body"] = (f"Hi {cust.contact_name or cust.name if cust else 'there'},\n\nPlease find attached the {docs} for {ref}"
                     + (f" (shipment {sh_codes})" if sh_codes else "") + ".\n\nLet us know if you have any questions.\n\n"
                     + "\n".join(x for x in [company.name, company.email, company.phone] if x))
        g["cc"] = ""
        g["ready"] = bool(g["attachments"])
        out.append(g)
    return out


def _files_for(db: Session, g: dict) -> List[tuple]:
    files, seen = [], set()
    for a in g["attachments"]:
        if a["name"] in seen:
            continue
        seen.add(a["name"])
        if a["kind"] == "packing_list":
            sh = next(ShipmentService.get(db, s["id"]) for s in g["shipments"] if a["name"] == f"Packing-List-{s['code']}.pdf")
            files.append((packing_list_pdf(db, sh), a["name"], "application/pdf"))
        elif a["kind"] == "labels":
            sh = next(ShipmentService.get(db, s["id"]) for s in g["shipments"] if a["name"] == f"Labels-{s['code']}.pdf")
            pdf = labels_pdf(db, sh)
            if pdf:
                files.append((pdf, a["name"], "application/pdf"))
        elif a["kind"] == "invoice":
            inv = next(InvoiceService.get(db, i["id"]) for i in g["invoices"] if a["name"] == f"{i['code']}.pdf")
            files.append((invoice_pdf(db, inv), a["name"], "application/pdf"))
    if any(a["kind"] == "pod" for a in g["attachments"]):
        for s in g["shipments"]:
            files += pod_files(db, ShipmentService.get(db, s["id"]))
    return files


def send(db: Session, groups: List[dict], edits: Dict[str, dict], sent_by: str) -> List[dict]:
    """Send every planned email (with the screen's edits by group key). One failing doesn't stop the others."""
    from app.services import email as email_service
    if not email_service.is_configured():
        raise HTTPException(status_code=400, detail="Email isn't set up yet. Add SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD (and SMTP_FROM) "
                                                    "to the AT-HUB .env file and restart the server.")
    results = []
    for g in groups:
        e = edits.get(g["key"], {})
        if e.get("skip"):
            results.append({"key": g["key"], "ok": False, "skipped": True, "detail": "Skipped"})
            continue
        to, cc = (e.get("to") if e.get("to") is not None else g["to"]), e.get("cc") or ""
        subject, body = e.get("subject") or g["subject"], e.get("body") or g["body"]
        try:
            files = _files_for(db, g)
            if not files:
                raise HTTPException(status_code=400, detail="Nothing to attach")
            rows = [("Customer PO #", g["po"])] if g["po"] else []
            rows += [("Shipment", ", ".join(s["code"] for s in g["shipments"]))] if g["shipments"] else []
            rows += [("Invoice", ", ".join(i["code"] for i in g["invoices"]))] if g["invoices"] else []
            to_list, cc_list = email_service._send(db, to, cc, subject, body, rows, files)
            names = ", ".join(f[1] for f in files)
            for i in g["invoices"]:
                inv = InvoiceService.get(db, i["id"])
                db.add(InvoiceEmail(invoice_id=inv.id, to_address=", ".join(to_list), cc_address=", ".join(cc_list) or None,
                                    subject=subject.strip(), body=body, sent_by=sent_by))
                if inv.status == "draft":
                    inv.status = "sent"
            for s in g["shipments"]:
                db.add(ShipmentEmail(shipment_id=s["id"], to_address=", ".join(to_list), cc_address=", ".join(cc_list) or None,
                                     subject=subject.strip(), files=names, sent_by=sent_by))
            db.commit()
            results.append({"key": g["key"], "ok": True, "detail": f"Sent to {', '.join(to_list)} · {len(files)} file(s)"})
        except HTTPException as ex:
            db.rollback()
            results.append({"key": g["key"], "ok": False, "detail": ex.detail})
    return results


def merged_pdf(db: Session, shipment_ids: List[int], invoice_ids: List[int], kinds: List[str], can_invoice: bool) -> bytes:
    """Every chosen document, record by record, in one PDF to print (PDF proofs of delivery included)."""
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for g in plan(db, shipment_ids, invoice_ids, kinds, group_by="shipment", can_invoice=can_invoice):
        for data, name, ctype in _files_for(db, g):
            if ctype == "application/pdf" or name.lower().endswith(".pdf"):
                for page in PdfReader(io.BytesIO(data)).pages:
                    writer.add_page(page)
    if not writer.pages:
        raise HTTPException(status_code=400, detail="None of the chosen documents exist for these records yet")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()

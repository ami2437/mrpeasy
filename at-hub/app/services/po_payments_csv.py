"""PO payments from MRPeasy's Purchase Orders list export (CSV): Number, Paid, Unpaid, Payment status,
Invoice ID / Invoice date / Due date (several per cell when a PO has several invoices).

MRPeasy's API has no purchase payments, but this export has what was paid per PO. For each PO we record
the difference between MRPeasy's Paid and what AT-HUB already holds -- so a second upload never
double-counts, and a payment entered in AT-HUB since is respected. The export has no payment dates:
the PO's latest invoice due date is used (never later than today) and the note says so.

The last applied file is kept at import-data/mrpeasy/po_payments.csv; every fresh MRPeasy import
applies it again (load.py), so the payments survive a re-import."""
import csv
import io
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models import PurchaseOrder, PurchaseOrderPayment, VendorBill

SAVED = Path(__file__).resolve().parents[2] / "import-data" / "mrpeasy" / "po_payments.csv"
NOTE = "From MRPeasy's PO export (Paid {paid}; {status}). The export has no payment date: invoice due date used."
TAG = "mrpeasy-po-export"


def _money(v) -> Optional[float]:
    try:
        return round(float(str(v).replace(",", "").replace("$", "").strip()), 2)
    except ValueError:
        return None


def _dates(cell) -> List[datetime]:
    out = []
    for part in str(cell or "").replace("\n", ",").split(","):
        part = part.strip()
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                out.append(datetime.strptime(part, fmt))
                break
            except ValueError:
                pass
    return out


def parse(data: bytes) -> List[dict]:
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or "Number" not in rows[0] or "Paid" not in rows[0]:
        raise ValueError("This doesn't look like MRPeasy's Purchase Orders export (needs Number and Paid columns)")
    head = rows[0]
    col = {name: head.index(name) for name in head if name}  # first occurrence (the export repeats "Status")
    out = []
    for r in rows[1:]:
        if not r or not r[col["Number"]].strip():
            continue
        g = lambda k: r[col[k]] if k in col and col[k] < len(r) else ""
        out.append({"code": g("Number").strip(), "total": _money(g("Total including tax")) or _money(g("Total")),
                    "paid": _money(g("Paid")) or 0, "unpaid": _money(g("Unpaid")), "payment_status": g("Payment status").strip(),
                    "invoices": [x.strip() for x in g("Invoice ID").replace("\n", ",").split(",") if x.strip()],
                    "invoice_dates": _dates(g("Invoice date")), "due_dates": _dates(g("Due date")), "order_date": _dates(g("Order date"))})
    return out


def plan(db: Session, rows: List[dict]) -> List[dict]:
    pos = {p.code: p for p in db.query(PurchaseOrder).all()}
    today = datetime.now()
    out = []
    for r in rows:
        po = pos.get(r["code"])
        row = {**{k: r[k] for k in ("code", "paid", "unpaid", "payment_status", "total")}, "invoices": r["invoices"]}
        if not po:
            out.append({**row, "action": "skip", "why": "no such PO in AT-HUB"})
            continue
        have = round(sum(p.amount for p in po.payments), 2)
        add = round(r["paid"] - have, 2)
        when = max(r["due_dates"] or r["invoice_dates"] or r["order_date"] or [today])
        bills = [b for b in po.bills]
        row.update(po_id=po.id, recorded=have, add=add, date=min(when, today).date().isoformat(),
                   bill=bills[0].bill_number if len(bills) == 1 else None)
        if add > 0.005:
            row.update(action="add", why=f"record ${add:,.2f}" + (f" on invoice {bills[0].bill_number}" if len(bills) == 1 else ""))
        elif add < -0.005:
            row.update(action="check", why=f"AT-HUB has ${have:,.2f} recorded, MRPeasy says ${r['paid']:,.2f} paid -- left as is")
        else:
            row.update(action="ok", why="already matches" if have else "nothing paid")
        out.append(row)
    return out


def apply(db: Session, rows: List[dict], by: str) -> dict:
    added = total = 0
    for r in plan(db, rows):
        if r["action"] != "add":
            continue
        po = db.query(PurchaseOrder).filter(PurchaseOrder.id == r["po_id"]).first()
        bill = db.query(VendorBill).filter(VendorBill.po_id == po.id, VendorBill.bill_number == r["bill"]).first() if r["bill"] else None
        # a single invoice the import brought in at $0 / less than what was paid can't hold the payment; leave it unlinked
        bill_id = bill.id if bill and bill.amount - bill.amount_paid >= r["add"] - 0.005 else None
        db.add(PurchaseOrderPayment(po_id=po.id, amount=r["add"], paid_date=datetime.fromisoformat(r["date"]), method=None,
                                    reference=TAG, vendor_bill_id=bill_id,
                                    note=NOTE.format(paid=f"${r['paid']:,.2f}", status=r["payment_status"] or "?"), created_by=by))
        added += 1
        total += r["add"]
    db.flush()
    return {"payments": added, "amount": round(total, 2)}

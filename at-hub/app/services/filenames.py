"""File names for the documents AT-HUB hands out (download, print-to-PDF, email, ZIP):

    <shipment #>-<customer PO #>-Packing List.pdf     SH215771-M219-30B-4156926-Packing List.pdf
    <shipment #>-<customer PO #>-Labels.pdf
    <invoice #>-<customer PO #>-Invoice.pdf           Inv-9601620-4156926-Invoice.pdf
No PO # on the order: that part is left out."""
import re
from urllib.parse import quote

from sqlalchemy.orm import Session

BAD = re.compile(r'[\\/:*?"<>|\r\n\t]+')  # not allowed in Windows file names


def clean(part) -> str:
    return BAD.sub("-", str(part or "")).strip(" .-")


def doc_name(code, po, what: str) -> str:
    base = "-".join(p for p in (clean(code), clean(po)) if p)
    return f"{base}-{what}.pdf" if base else f"{what}.pdf"


def _po(db: Session, order_id):
    from app.models import CustomerOrder
    order = db.get(CustomerOrder, order_id) if order_id else None
    return order.po_number if order else None


def packing_list_name(db: Session, shipment) -> str:
    return doc_name(shipment.code, _po(db, shipment.order_id), "Packing List")


def labels_name(db: Session, shipment) -> str:
    return doc_name(shipment.code, _po(db, shipment.order_id), "Labels")


def invoice_name(db: Session, invoice) -> str:
    return doc_name(invoice.code, _po(db, invoice.order_id), "Invoice")


def disposition(name: str, inline: bool = True) -> str:
    """Content-Disposition with the name (an ASCII copy for old clients, the exact one as filename*)."""
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "-")
    return f"{'inline' if inline else 'attachment'}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"

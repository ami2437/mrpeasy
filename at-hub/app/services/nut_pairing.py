"""Nuts travel with their bolts: on a shipment, the nut line for a bolt (15420-NUT for 15420, 15420-NUTS too) is
listed right under that bolt, and goes on the bolt's pallet unless a pallet was set for the nut itself.

    line_order(lines, code_of)       -> order lines in customer-order order, each nut under its bolt
    pallets_by_line(shipment, codes) -> {order_line_id: [pallet #s]} with nuts inheriting their bolt's
    fill_nut_pallets(shipment, codes)-> writes the inherited pallet onto the nut's boxes (labels match too)
"""
from typing import Callable, Dict, List, Optional

from app.services.item_naming import normalize_code

NUT = "-NUT"


def bolt_code(code: str) -> Optional[str]:
    """'15420-NUT' / '15420-NUTS' -> '15420'; anything else -> None."""
    c = normalize_code(code or "").upper()
    return c[: -len(NUT)] if c.endswith(NUT) and len(c) > len(NUT) else None


def line_order(lines: List, code_of: Callable) -> List:
    """Customer-order order (line #), then every nut moved directly under its bolt when the bolt is here too."""
    base = sorted(lines, key=lambda l: (l.line_no or 0, l.id))
    by_code = {}
    for l in base:
        by_code.setdefault(normalize_code(code_of(l) or "").upper(), l)
    nuts_of = {}
    for l in base:
        b = bolt_code(code_of(l))
        if b and b in by_code and by_code[b] is not l:
            nuts_of.setdefault(by_code[b].id, []).append(l)
    moved = {n.id for ns in nuts_of.values() for n in ns}
    out = []
    for l in base:
        if l.id in moved:
            continue
        out.append(l)
        out += nuts_of.get(l.id, [])
    return out


def _codes_by_line(shipment, item_code: Dict[int, str]) -> Dict[int, str]:
    out = {}
    for sl in shipment.lines:
        out[sl.order_line_id] = normalize_code(item_code.get(sl.item_id, "") or "").upper()
    return out


def pallets_by_line(shipment, item_code: Dict[int, str]) -> Dict[int, List[str]]:
    """Pallet #s per order line, from its boxes; a nut line with none takes its bolt line's."""
    own: Dict[int, List[str]] = {}
    for b in sorted(shipment.boxes, key=lambda b: b.box_number or 0):
        if b.pallet_number:
            ps = own.setdefault(b.order_line_id, [])
            if b.pallet_number not in ps:
                ps.append(b.pallet_number)
    codes = _codes_by_line(shipment, item_code)
    line_of_code = {}
    for lid, c in codes.items():
        line_of_code.setdefault(c, lid)
    out = dict(own)
    for lid, c in codes.items():
        if out.get(lid):
            continue
        b = bolt_code(c)
        if b and own.get(line_of_code.get(b)):
            out[lid] = list(own[line_of_code[b]])
    return out


def fill_nut_pallets(shipment, item_code: Dict[int, str]) -> int:
    """Nut boxes without a pallet get their bolt's (its first pallet). Returns how many boxes changed."""
    pallets = pallets_by_line(shipment, item_code)
    n = 0
    for b in shipment.boxes:
        if not b.pallet_number and pallets.get(b.order_line_id):
            b.pallet_number = pallets[b.order_line_id][0]
            n += 1
    return n

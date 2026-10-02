"""Exact readers for customer PO layouts we receive often, tried before the AI model.

A template reads its layout deterministically -- every number checked (unit x qty = extended)
-- so it's faster and more accurate than a general model on the same PDF. Anything it doesn't
recognise falls through to the AI.

Chart / Hudson Products POs run the columns together in the PDF text, e.g.
    "Oct 8, 2026 EA 3.8500 192.5050.00"  = dock date, UOM, unit 3.8500, extended 192.50, qty 50.00
which a general model misreads; here they are split by their fixed decimals.
"""
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

MONTH_DATE = r"[A-Z][a-z]{2} \d{1,2}, \d{4}"
# "1.000 31181" ... "Oct 8, 2026 EA 3.8500 192.5050.00"
CHART_LINE = re.compile(
    r"^(\d+)\.000 (\S+)\n(.*?)\n(" + MONTH_DATE + r") (\S+) ([\d,]*\.\d{4}) ([\d,.]+)\n", re.M | re.S)
JOB_RE = re.compile(r"^[A-Z0-9][A-Z0-9/.-]{2,24}$")


def _date(text: str) -> Optional[str]:
    try:
        return datetime.strptime(text.strip(), "%b %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _split_ext_qty(glued: str, unit: float):
    """'192.5050.00' -> (192.50, 50.00): extended has 2 decimals, then the quantity follows.
    Tries every split and keeps the one where unit x qty = extended."""
    best = None
    for m in re.finditer(r"\.\d{2}", glued):
        ext_s, qty_s = glued[:m.end()], glued[m.end():]
        if not qty_s or not re.fullmatch(r"[\d,]*\.?\d*", qty_s):
            continue
        try:
            ext, qty = _num(ext_s), _num(qty_s)
        except ValueError:
            continue
        err = abs(unit * qty - ext)
        if best is None or err < best[2]:
            best = (ext, qty, err)
    return best


def parse_chart(text: str) -> Optional[Dict[str, Any]]:
    if "HUDSON PRODUCTS CORPORATION" not in text or "Order Number:" not in text:
        return None
    body = text.split("Chart Standard Terms & Conditions")[0]  # 40 KB of T&Cs after the order
    header = re.search(r"\d+ of \d+\n(" + MONTH_DATE + r")\n(\d+)\n(\d+)\n", body)
    ship = re.search(r"SHIP TO:\n(.*?)\n\d+ of \d+\n", body, re.S)
    lines, problems, skipped = [], [], []
    for m in CHART_LINE.finditer(body):
        line_no, item, middle, dock, uom, unit_s, glued = m.groups()
        unit = _num(unit_s)
        split = _split_ext_qty(glued, unit)
        if not split:
            problems.append(f"line {line_no}: couldn't read quantity from '{glued}'")
            continue
        ext, qty, err = split
        if err > 0.02 + 0.005 * ext:
            problems.append(f"line {line_no}: unit x qty ({unit} x {qty:g}) doesn't equal extended {ext}")
        mid = [x.strip() for x in middle.split("\n") if x.strip()]
        job = mid.pop() if len(mid) > 1 and JOB_RE.match(mid[-1]) and "_" not in mid[-1] else None
        lines.append({"line_no": int(line_no), "item_code": item, "customer_item_code": item,
                      "description": " ".join(mid), "quantity": qty, "unit": uom, "unit_price": unit,
                      "extended": ext, "delivery_date": _date(dock), "job_number": job})
    if not lines:
        return None
    # A kit header line uses the job # as its item # (e.g. "M170-30C FIELD ERECTION BOLT KIT", 1 x $0.01):
    # it isn't a product, so it's left off the order.
    job_codes = {l["job_number"] for l in lines if l["job_number"]}
    looks_like_job = lambda code: code in job_codes or (re.search(r"[A-Za-z]", code) and "-" in code and not code.upper().endswith("-HPC"))
    for l in [l for l in lines if l["unit_price"] <= 0.01 and looks_like_job(l["item_code"])]:
        lines.remove(l)
        skipped.append(f"Skipped line {l['line_no']} ({l['item_code']} {l['description']}, {l['quantity']:g} x ${l['unit_price']}): "
                        "a kit header for the job, not a product")
    total = re.search(r"Total Order \(USD\)\n(?:[\d,.]+\n){2}([\d,.]+)", body)
    jobs = [l["job_number"] for l in lines if l["job_number"]]
    dock_dates = [l["delivery_date"] for l in lines if l["delivery_date"]]
    if total and abs(sum(l["extended"] for l in lines) - _num(total.group(1))) > 0.02:
        problems.append(f"lines add up to {sum(l['extended'] for l in lines):,.2f} but the PO total is {total.group(1)}")
    return {
        "template": "Chart / Hudson Products",
        "customer_name": "Hudson Products",
        "po_number": header.group(2) if header else None,
        "order_date": _date(header.group(1)) if header else None,
        "delivery_date": min(dock_dates) if dock_dates else None,
        "ship_to_address": "\n".join(x.strip() for x in ship.group(1).split("\n") if x.strip() and x.strip() != "United States") if ship else None,
        "job_number": max(set(jobs), key=jobs.count) if jobs else None,
        "notes": None,
        "lines": lines,
        "problems": problems,
        "skipped": skipped,
    }


TEMPLATES = [parse_chart]


def parse(text: str) -> Optional[Dict[str, Any]]:
    for template in TEMPLATES:
        result = template(text)
        if result:
            return result
    return None

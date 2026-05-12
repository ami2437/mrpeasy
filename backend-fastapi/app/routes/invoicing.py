from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from app.services.mrpeasy_client import mrpeasy_client

try:
    import pandas as pd
except Exception:  # pragma: no cover - handled at runtime when feature is used
    pd = None

router = APIRouter(prefix="/api/invoicing", tags=["invoicing"])

_generated_invoice_drafts = []
_generated_invoice_keys = set()
_bulk_payment_field_operations = {}

_BULK_REQUIRED_COLUMNS = {
    "item number": "Item Number",
    "disbursement date": "Disbursement Date",
    "funding amount": "Funding Amount",
    "discount": "Discount"
}


class InvoiceGenerationItemSelection(BaseModel):
    order_code: str
    item_code: str
    line_key: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    line_note: Optional[str] = None


class InvoiceGenerationManualLine(BaseModel):
    order_code: str
    description: str
    quantity: Optional[float] = 1
    unit_price: Optional[float] = 0
    shipment_number: Optional[str] = None
    delivery_date: Optional[str] = None
    line_note: Optional[str] = None


class InvoiceGenerationRequest(BaseModel):
    order_codes: List[str] = Field(default_factory=list)
    selected_items: List[InvoiceGenerationItemSelection] = Field(default_factory=list)
    manual_lines: List[InvoiceGenerationManualLine] = Field(default_factory=list)
    selection_applied: bool = False
    generation_mode: Optional[str] = "order"


def _ensure_bulk_dependencies():
    if pd is None:
        raise HTTPException(
            status_code=500,
            detail="Excel upload dependencies are missing. Install pandas and openpyxl."
        )


def _normalize_invoice_code(code: Any) -> str:
    normalized = str(code or "").strip()
    if normalized.upper().startswith("INV-"):
        return f"Inv-{normalized[4:]}"
    return normalized


def _parse_date_to_unix(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and value > 1000000000:
        return int(value)

    text = str(value).strip()
    if not text:
        return None

    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return int(parsed.timestamp())
        except ValueError:
            continue

    try:
        parsed = pd.to_datetime(value)
        if pd.isna(parsed):
            return None
        return int(parsed.to_pydatetime().timestamp())
    except Exception:
        return None


def _detect_header_row(raw_df) -> Optional[int]:
    for idx in range(min(len(raw_df), 30)):
        row_values = [str(v).strip().lower() for v in raw_df.iloc[idx].tolist() if str(v).strip()]
        row_set = set(row_values)
        if all(key in row_set for key in _BULK_REQUIRED_COLUMNS.keys()):
            return idx
    return None


def _load_bulk_payment_rows(file_bytes: bytes, filename: str) -> List[Dict[str, Any]]:
    _ensure_bulk_dependencies()

    lower_name = (filename or "").lower()
    if lower_name.endswith(".csv"):
        frame = pd.read_csv(BytesIO(file_bytes))
    else:
        workbook = pd.ExcelFile(BytesIO(file_bytes))
        sheet_name = "Associated Items" if "Associated Items" in workbook.sheet_names else workbook.sheet_names[0]
        raw = pd.read_excel(workbook, sheet_name=sheet_name, header=None)
        header_row = _detect_header_row(raw)
        if header_row is None:
            raise HTTPException(
                status_code=400,
                detail="Could not detect header row. Expected columns: Item Number, Disbursement Date, Funding Amount, Discount."
            )
        frame = pd.read_excel(workbook, sheet_name=sheet_name, header=header_row)

    frame.columns = [str(column).strip() for column in frame.columns]
    normalized_column_map = {str(column).strip().lower(): column for column in frame.columns}
    missing = [original for key, original in _BULK_REQUIRED_COLUMNS.items() if key not in normalized_column_map]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required columns: {', '.join(missing)}")

    item_number_col = normalized_column_map["item number"]
    disbursement_col = normalized_column_map["disbursement date"]
    funding_col = normalized_column_map["funding amount"]
    discount_col = normalized_column_map["discount"]

    if "item type" in normalized_column_map:
        type_col = normalized_column_map["item type"]
        frame = frame[frame[type_col].astype(str).str.strip().str.lower() == "invoice"]

    frame = frame[frame[item_number_col].notna()].copy()

    rows = []
    for _, row in frame.iterrows():
        invoice_code = _normalize_invoice_code(row.get(item_number_col))
        if not invoice_code:
            continue

        rows.append({
            "invoice_code": invoice_code,
            "disbursement_date_raw": row.get(disbursement_col),
            "funding_amount_raw": row.get(funding_col),
            "discount_raw": row.get(discount_col)
        })

    return rows


def _build_bulk_payment_preview(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    invoices = mrpeasy_client.get_invoices({"limit": 1000}) or []
    invoices_by_code = {
        str(inv.get("code", "")).strip().lower(): inv
        for inv in invoices
        if isinstance(inv, dict)
    }

    preview_rows = []
    valid_count = 0

    for row in rows:
        invoice_code = row.get("invoice_code", "")
        invoice = invoices_by_code.get(invoice_code.lower())

        funding_amount = _to_number(row.get("funding_amount_raw"), 0)
        discount_amount = _to_number(row.get("discount_raw"), 0)
        disbursement_unix = _parse_date_to_unix(row.get("disbursement_date_raw"))

        parse_errors = []
        if disbursement_unix is None:
            parse_errors.append("Invalid Disbursement Date")

        if funding_amount <= 0:
            parse_errors.append("Funding Amount must be greater than 0")

        if discount_amount < 0:
            parse_errors.append("Discount cannot be negative")

        preview = {
            "invoice_code": invoice_code,
            "invoice_found": invoice is not None,
            "invoice_id": invoice.get("invoice_id") if invoice else None,
            "invoice_total": _to_number(invoice.get("total_price"), 0) if invoice else None,
            "status": str(invoice.get("status")) if invoice else None,
            "status_txt": invoice.get("status_txt") if invoice else None,
            "custom_570": disbursement_unix,
            "custom_571": f"{funding_amount:.2f}",
            "custom_572": f"{discount_amount:.2f}",
            "parse_errors": parse_errors
        }

        if invoice:
            invoice_total = _to_number(invoice.get("total_price"), 0)
            preview["sum_check"] = round(funding_amount + discount_amount, 2)
            preview["matches_invoice_total"] = abs(preview["sum_check"] - round(invoice_total, 2)) < 0.01
        else:
            preview["sum_check"] = round(funding_amount + discount_amount, 2)
            preview["matches_invoice_total"] = False

        if preview["invoice_found"] and not parse_errors:
            valid_count += 1

        preview_rows.append(preview)

    return {
        "total_rows": len(preview_rows),
        "valid_rows": valid_count,
        "invalid_rows": len(preview_rows) - valid_count,
        "rows": preview_rows
    }


@router.post("/bulk-payment-fields/preview")
async def preview_bulk_payment_field_upload(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="File name is required")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    rows = _load_bulk_payment_rows(file_bytes, file.filename)
    preview = _build_bulk_payment_preview(rows)
    return {
        "file_name": file.filename,
        **preview
    }


@router.post("/bulk-payment-fields/apply")
async def apply_bulk_payment_field_upload(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="File name is required")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    rows = _load_bulk_payment_rows(file_bytes, file.filename)
    preview = _build_bulk_payment_preview(rows)
    valid_rows = [row for row in preview["rows"] if row["invoice_found"] and not row["parse_errors"]]

    if not valid_rows:
        raise HTTPException(status_code=400, detail="No valid invoice rows found to update")

    operation_id = str(uuid4())
    operation = {
        "operation_id": operation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "file_name": file.filename,
        "rolled_back": False,
        "updates": [],
        "skipped_matching": [],
        "discrepancies": []
    }

    rollback_errors = []

    try:
        for row in valid_rows:
            invoice_id = row["invoice_id"]
            before = mrpeasy_client.get_invoice(invoice_id) or {}

            payload = {
                "custom_570": row["custom_570"],
                "custom_571": row["custom_571"],
                "custom_572": row["custom_572"]
            }

            rollback_payload = {
                "custom_570": before.get("custom_570"),
                "custom_571": before.get("custom_571"),
                "custom_572": before.get("custom_572")
            }

            if _fields_are_all_empty(before):
                mrpeasy_client.update_invoice(invoice_id, payload)
                after = mrpeasy_client.get_invoice(invoice_id) or {}

                operation["updates"].append({
                    "invoice_id": invoice_id,
                    "invoice_code": row["invoice_code"],
                    "payload": payload,
                    "rollback_payload": rollback_payload,
                    "after": {
                        "status": after.get("status"),
                        "status_txt": after.get("status_txt"),
                        "custom_570": after.get("custom_570"),
                        "custom_571": after.get("custom_571"),
                        "custom_572": after.get("custom_572")
                    }
                })
                continue

            if _fields_match_expected(before, payload):
                operation["skipped_matching"].append({
                    "invoice_id": invoice_id,
                    "invoice_code": row["invoice_code"],
                    "reason": "Existing custom fields already match file values",
                    "existing": rollback_payload
                })
                continue

            operation["discrepancies"].append({
                "invoice_id": invoice_id,
                "invoice_code": row["invoice_code"],
                "reason": "Existing custom fields differ from file values",
                "existing": rollback_payload,
                "expected": payload
            })
    except Exception as exc:
        for applied in reversed(operation["updates"]):
            try:
                mrpeasy_client.update_invoice(applied["invoice_id"], applied["rollback_payload"])
            except Exception as rollback_exc:
                rollback_errors.append({
                    "invoice_id": applied["invoice_id"],
                    "invoice_code": applied["invoice_code"],
                    "error": str(rollback_exc)
                })

        operation["rolled_back"] = True
        operation["rollback_errors"] = rollback_errors
        _bulk_payment_field_operations[operation_id] = operation

        raise HTTPException(
            status_code=500,
            detail={
                "message": f"Bulk update failed and rollback was attempted: {exc}",
                "operation_id": operation_id,
                "updated_before_failure": len(operation["updates"]),
                "rollback_errors": rollback_errors
            }
        )

    _bulk_payment_field_operations[operation_id] = operation

    skipped_existing_count = len(operation["skipped_matching"])
    discrepancy_count = len(operation["discrepancies"])
    skipped_count = preview["invalid_rows"] + skipped_existing_count + discrepancy_count

    return {
        "operation_id": operation_id,
        "file_name": file.filename,
        "updated_count": len(operation["updates"]),
        "skipped_count": skipped_count,
        "skipped_invalid_count": preview["invalid_rows"],
        "skipped_existing_match_count": skipped_existing_count,
        "discrepancy_count": discrepancy_count,
        "skipped_existing_matches": operation["skipped_matching"],
        "discrepancies": operation["discrepancies"],
        "rolled_back": False,
        "updates": operation["updates"]
    }


@router.post("/bulk-payment-fields/rollback/{operation_id}")
def rollback_bulk_payment_field_operation(operation_id: str):
    operation = _bulk_payment_field_operations.get(operation_id)
    if not operation:
        raise HTTPException(status_code=404, detail="Operation not found")

    if operation.get("rolled_back"):
        return {
            "operation_id": operation_id,
            "rolled_back": True,
            "message": "Operation was already rolled back"
        }

    rollback_errors = []
    reverted = 0

    for applied in reversed(operation.get("updates", [])):
        try:
            mrpeasy_client.update_invoice(applied["invoice_id"], applied["rollback_payload"])
            reverted += 1
        except Exception as exc:
            rollback_errors.append({
                "invoice_id": applied.get("invoice_id"),
                "invoice_code": applied.get("invoice_code"),
                "error": str(exc)
            })

    operation["rolled_back"] = True
    operation["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
    operation["rollback_errors"] = rollback_errors

    return {
        "operation_id": operation_id,
        "rolled_back": True,
        "reverted_count": reverted,
        "rollback_errors": rollback_errors
    }


@router.get("/bulk-payment-fields/operations")
def list_bulk_payment_field_operations(limit: int = 20):
    safe_limit = max(1, min(int(limit or 20), 200))

    operations = []
    for operation_id, op in _bulk_payment_field_operations.items():
        updates = op.get("updates", [])
        rollback_errors = op.get("rollback_errors", [])
        skipped_matching = op.get("skipped_matching", [])
        discrepancies = op.get("discrepancies", [])
        operations.append({
            "operation_id": operation_id,
            "created_at": op.get("created_at"),
            "file_name": op.get("file_name"),
            "rolled_back": bool(op.get("rolled_back")),
            "rolled_back_at": op.get("rolled_back_at"),
            "updated_count": len(updates),
            "skipped_existing_match_count": len(skipped_matching),
            "discrepancy_count": len(discrepancies),
            "rollback_error_count": len(rollback_errors)
        })

    operations.sort(key=lambda item: item.get("created_at") or "", reverse=True)

    return {
        "count": min(len(operations), safe_limit),
        "operations": operations[:safe_limit]
    }


def _to_number(value, default=0.0):
    if value is None:
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned == "":
            return float(default)
        try:
            return float(cleaned)
        except ValueError:
            return float(default)
    return float(default)


def _is_empty_custom_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _normalize_unix_like(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except Exception:
        return None


def _normalize_money_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return f"{_to_number(value, 0):.2f}"


def _fields_are_all_empty(invoice: Dict[str, Any]) -> bool:
    return (
        _is_empty_custom_value(invoice.get("custom_570"))
        and _is_empty_custom_value(invoice.get("custom_571"))
        and _is_empty_custom_value(invoice.get("custom_572"))
    )


def _fields_match_expected(invoice: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    existing_570 = _normalize_unix_like(invoice.get("custom_570"))
    expected_570 = _normalize_unix_like(expected.get("custom_570"))
    existing_571 = _normalize_money_string(invoice.get("custom_571"))
    expected_571 = _normalize_money_string(expected.get("custom_571"))
    existing_572 = _normalize_money_string(invoice.get("custom_572"))
    expected_572 = _normalize_money_string(expected.get("custom_572"))

    return (
        existing_570 == expected_570
        and existing_571 == expected_571
        and existing_572 == expected_572
    )


def _extract_job_number(order):
    candidate = order.get("custom_814")
    if candidate is None:
        candidate = order.get("custom_531")

    if candidate is None:
        return "N/A"

    if isinstance(candidate, (int, float)):
        numeric = int(candidate)
        if numeric < 1000000000:
            return str(numeric)
        return "N/A"

    if isinstance(candidate, str):
        trimmed = candidate.strip()
        if not trimmed:
            return "N/A"

        lower_trimmed = trimmed.lower()
        if lower_trimmed in {"partial", "complete", "open"}:
            return "N/A"

        if trimmed.isdigit() and int(trimmed) >= 1000000000:
            return "N/A"

        return trimmed

    return "N/A"


def _has_zero_selling_price(item_price, total_price, quantity):
    if item_price > 0:
        return False

    if total_price > 0:
        return False

    if quantity <= 0:
        return True

    return True


def _build_order_reference_map():
    orders = mrpeasy_client.get_customer_orders() or []
    reference_by_order_id = {}

    for order in orders:
        order_id = order.get("cust_ord_id")
        if order_id is None:
            continue

        reference = order.get("reference")
        if reference is None:
            continue

        if isinstance(reference, str):
            trimmed = reference.strip()
            if not trimmed:
                continue
            reference_by_order_id[order_id] = trimmed
        else:
            reference_by_order_id[order_id] = str(reference)

    return reference_by_order_id


def _enrich_invoices_with_reference(invoices):
    if not invoices:
        return invoices

    reference_by_order_id = _build_order_reference_map()

    for invoice in invoices:
        if not isinstance(invoice, dict):
            continue

        existing_reference = invoice.get("reference")
        if isinstance(existing_reference, str) and existing_reference.strip():
            continue
        if existing_reference is not None and not isinstance(existing_reference, str):
            continue

        cust_ord_id = invoice.get("cust_ord_id")
        if cust_ord_id in reference_by_order_id:
            invoice["reference"] = reference_by_order_id[cust_ord_id]

    return invoices


def _format_epoch_to_iso(value):
    if value is None:
        return None

    as_string = str(value).strip()
    if not as_string:
        return None

    try:
        epoch = int(float(as_string))
        return datetime.fromtimestamp(epoch, tz=timezone.utc).date().isoformat()
    except Exception:
        return as_string


def _build_invoice_match_key(product):
    article_id = product.get("article_id")
    if article_id not in (None, ""):
        return ("article_id", str(article_id))

    item_code = product.get("item_code")
    if item_code not in (None, ""):
        return ("item_code", str(item_code))

    return None


def _build_discrepancy_line_key(order_code, product, line_index):
    order_line = str(product.get("ord") or product.get("order_line") or "1")
    delivery_date = _format_epoch_to_iso(product.get("delivery_date")) or "NA"
    article_id = product.get("article_id")
    item_code = str(product.get("item_code") or "NA")
    article_or_item = f"A{article_id}" if article_id not in (None, "") else f"I{item_code}"
    normalized_order = str(order_code or "N/A")
    return f"{normalized_order}|{article_or_item}|{order_line}|{delivery_date}|{line_index}"


def _build_order_shipment_map():
    shipments = mrpeasy_client.get_shipments() or []
    shipment_map = {}

    for shipment in shipments:
        shipment_code = shipment.get("code") or "N/A"
        delivery_date = _format_epoch_to_iso(shipment.get("delivery_date"))
        shipment_product_match_keys = set()
        for shipment_product in shipment.get("products", []) or []:
            match_key = _build_invoice_match_key(shipment_product)
            if match_key:
                shipment_product_match_keys.add(match_key)

        order_ids = set()
        direct_id = shipment.get("customer_order_id")
        if direct_id is not None:
            order_ids.add(direct_id)

        direct_cust_ord_id = shipment.get("cust_ord_id")
        if direct_cust_ord_id is not None:
            order_ids.add(direct_cust_ord_id)

        for linked in shipment.get("orders", []) or []:
            linked_id = linked.get("customer_order_id")
            if linked_id is not None:
                order_ids.add(linked_id)

        for order_id in order_ids:
            if order_id not in shipment_map:
                shipment_map[order_id] = {
                    "shipment_codes": [],
                    "delivery_dates": [],
                    "by_delivery_date": {},
                    "shipment_entries": []
                }

            if shipment_code not in shipment_map[order_id]["shipment_codes"]:
                shipment_map[order_id]["shipment_codes"].append(shipment_code)
            if delivery_date and delivery_date not in shipment_map[order_id]["delivery_dates"]:
                shipment_map[order_id]["delivery_dates"].append(delivery_date)

            if delivery_date:
                by_date = shipment_map[order_id]["by_delivery_date"]
                if delivery_date not in by_date:
                    by_date[delivery_date] = []
                if shipment_code not in by_date[delivery_date]:
                    by_date[delivery_date].append(shipment_code)

            shipment_entries = shipment_map[order_id]["shipment_entries"]
            existing_entry = next((entry for entry in shipment_entries if entry.get("code") == shipment_code), None)
            if existing_entry:
                existing_entry["product_match_keys"].update(shipment_product_match_keys)
            else:
                shipment_entries.append({
                    "code": shipment_code,
                    "delivery_date": delivery_date,
                    "product_match_keys": set(shipment_product_match_keys)
                })

    return shipment_map


def _build_not_invoiced_candidates(selected_order_codes: Optional[List[str]] = None):
    discrepancy_data = get_shipped_uninvoiced_items()
    not_invoiced_orders = discrepancy_data.get("not_invoiced", {}).get("orders", [])
    shipment_map = _build_order_shipment_map()

    selected_set = set(code.strip() for code in (selected_order_codes or []) if isinstance(code, str) and code.strip())

    candidates = []
    for order_data in not_invoiced_orders:
        order = order_data.get("order", {})
        order_code = order.get("code")
        if selected_set and order_code not in selected_set:
            continue

        order_shipments = shipment_map.get(order.get("cust_ord_id"), {})
        shipment_codes = order_shipments.get("shipment_codes", [])
        delivery_dates = order_shipments.get("delivery_dates", [])
        shipment_codes_by_delivery = order_shipments.get("by_delivery_date", {})
        shipment_entries = order_shipments.get("shipment_entries", [])

        def _parse_iso_date(value):
            if not value or not isinstance(value, str):
                return None
            try:
                return datetime.fromisoformat(value).date()
            except Exception:
                return None

        def _resolve_line_shipment_codes(item):
            line_article_id = item.get("article_id")
            if line_article_id not in (None, ""):
                line_match_key = ("article_id", str(line_article_id))
            else:
                line_item_code = item.get("item_code")
                line_match_key = ("item_code", str(line_item_code)) if line_item_code not in (None, "") else None

            line_delivery_date = item.get("delivery_date")
            line_delivery_parsed = _parse_iso_date(line_delivery_date)

            candidate_entries = []
            if line_match_key:
                candidate_entries = [
                    entry for entry in shipment_entries
                    if line_match_key in entry.get("product_match_keys", set())
                ]
            if not candidate_entries:
                candidate_entries = list(shipment_entries)

            if candidate_entries:
                if line_delivery_parsed:
                    scored = []
                    for entry in candidate_entries:
                        entry_delivery = _parse_iso_date(entry.get("delivery_date"))
                        if entry_delivery is None:
                            distance = 10**6
                        else:
                            distance = abs((entry_delivery - line_delivery_parsed).days)
                        scored.append((distance, entry.get("code") or "N/A"))
                    scored.sort(key=lambda pair: (pair[0], pair[1]))
                    return [scored[0][1]] if scored else []

                candidate_codes = sorted({entry.get("code") or "N/A" for entry in candidate_entries})
                return [candidate_codes[0]] if candidate_codes else []

            if line_delivery_date and line_delivery_date in shipment_codes_by_delivery:
                by_date_codes = shipment_codes_by_delivery.get(line_delivery_date, [])
                if by_date_codes:
                    return [by_date_codes[0]]

            if shipment_codes:
                return [sorted(shipment_codes)[0]]

            return []

        discrepancy_items = order_data.get("discrepancy_items", [])
        not_invoiced_items = [
            item for item in discrepancy_items
            if item.get("discrepancy_type") == "not_invoiced"
        ]
        if not not_invoiced_items:
            continue

        lines = []
        subtotal = 0.0
        for item in not_invoiced_items:
            quantity = abs(_to_number(item.get("discrepancy", 0), 0))
            if quantity <= 0:
                continue

            unit_price = _to_number(item.get("item_price", 0), 0)
            line_total = quantity * unit_price
            subtotal += line_total
            line_key = item.get("line_key") or f"{order_code}:{item.get('item_code')}:{item.get('line_index', 0)}"

            line_delivery_date = item.get("delivery_date")
            line_shipment_codes = _resolve_line_shipment_codes(item)

            lines.append({
                "line_key": line_key,
                "item_code": item.get("item_code"),
                "article_id": item.get("article_id"),
                "item_title": item.get("item_title"),
                "order_line": item.get("order_line", "1"),
                "line_index": item.get("line_index", 0),
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
                "shipment_number": ", ".join(line_shipment_codes) if line_shipment_codes else "N/A",
                "delivery_date": line_delivery_date or (", ".join(delivery_dates) if delivery_dates else "N/A")
            })

        shipping = _to_number(order.get("shipping_cost", 0), 0)
        total = subtotal + shipping

        candidates.append({
            "order": {
                "cust_ord_id": order.get("cust_ord_id"),
                "code": order_code,
                "customer_name": order.get("customer_name"),
                "reference": order.get("reference") or "N/A",
                "job_number": order.get("job_number") or "N/A",
                "currency": order.get("currency") or "$",
                "shipment_number": ", ".join(shipment_codes) if shipment_codes else "N/A",
                "delivery_date": ", ".join(delivery_dates) if delivery_dates else "N/A"
            },
            "invoice": {
                "lines": lines,
                "shipping": shipping,
                "subtotal": subtotal,
                "total": total
            }
        })

    return candidates


@router.post("/generator/preview")
def preview_generated_invoices(payload: InvoiceGenerationRequest):
    try:
        candidates = _build_not_invoiced_candidates(payload.order_codes)
        return {
            "candidates": candidates,
            "count": len(candidates)
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build invoice preview: {exc}")


@router.post("/generator/create-drafts")
def create_generated_invoice_drafts(payload: InvoiceGenerationRequest):
    try:
        candidates = _build_not_invoiced_candidates(payload.order_codes)
        manual_line_map = {}
        for manual_line in payload.manual_lines:
            order_code = (manual_line.order_code or "").strip()
            description = (manual_line.description or "").strip()
            if not order_code or not description:
                continue

            quantity = _to_number(manual_line.quantity, 1)
            unit_price = _to_number(manual_line.unit_price, 0)
            if quantity <= 0:
                continue

            if order_code not in manual_line_map:
                manual_line_map[order_code] = []

            manual_line_map[order_code].append({
                "item_code": "",
                "item_title": description,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": quantity * unit_price,
                "shipment_number": (manual_line.shipment_number or "").strip() or "N/A",
                "delivery_date": (manual_line.delivery_date or "").strip() or "N/A",
                "line_note": (manual_line.line_note or "").strip(),
                "line_type": "manual"
            })

        selected_item_map = {}
        for selected_item in payload.selected_items:
            line_key = (selected_item.line_key or "").strip()
            fallback_key = f"{selected_item.order_code}:{selected_item.item_code}"
            key = line_key or fallback_key
            selected_item_map[key] = {
                "quantity": selected_item.quantity,
                "unit_price": selected_item.unit_price,
                "line_note": selected_item.line_note
            }

        if payload.selection_applied:
            filtered_candidates = []
            for candidate in candidates:
                order = candidate.get("order", {})
                order_code = order.get("code")
                lines = candidate.get("invoice", {}).get("lines", [])
                selected_lines = []

                for line in lines:
                    line_key = (line.get("line_key") or "").strip()
                    legacy_key = f"{order_code}:{line.get('item_code')}"
                    override = selected_item_map.get(line_key) if line_key else None
                    if override is None:
                        override = selected_item_map.get(legacy_key)
                    if override is None:
                        continue

                    quantity = _to_number(override.get("quantity"), line.get("quantity", 0))
                    unit_price = _to_number(override.get("unit_price"), line.get("unit_price", 0))
                    if quantity <= 0:
                        continue

                    selected_lines.append({
                        **line,
                        "quantity": quantity,
                        "unit_price": unit_price,
                        "line_total": quantity * unit_price,
                        "line_note": (override.get("line_note") or "").strip()
                    })

                selected_lines.extend(manual_line_map.get(order_code, []))
                if not selected_lines:
                    continue

                shipping = _to_number(candidate.get("invoice", {}).get("shipping", 0), 0)
                subtotal = sum(_to_number(line.get("line_total", 0), 0) for line in selected_lines)
                total = subtotal + shipping
                filtered_candidates.append({
                    "order": order,
                    "invoice": {
                        "lines": selected_lines,
                        "shipping": shipping,
                        "subtotal": subtotal,
                        "total": total
                    }
                })

            candidates = filtered_candidates
        elif manual_line_map:
            augmented_candidates = []
            for candidate in candidates:
                order = candidate.get("order", {})
                order_code = order.get("code")
                invoice = candidate.get("invoice", {})
                lines = list(invoice.get("lines", []))
                lines.extend(manual_line_map.get(order_code, []))

                shipping = _to_number(invoice.get("shipping", 0), 0)
                subtotal = sum(_to_number(line.get("line_total", 0), 0) for line in lines)
                total = subtotal + shipping

                augmented_candidates.append({
                    "order": order,
                    "invoice": {
                        "lines": lines,
                        "shipping": shipping,
                        "subtotal": subtotal,
                        "total": total
                    }
                })

            candidates = augmented_candidates

        generation_mode = (payload.generation_mode or "order").strip().lower()
        if generation_mode not in {"order", "po"}:
            generation_mode = "order"

        grouped_candidates = {}
        for candidate in candidates:
            order = candidate.get("order", {})
            invoice = candidate.get("invoice", {})

            if generation_mode == "po":
                reference = (order.get("reference") or "").strip()
                if not reference or reference.upper() == "N/A":
                    group_key = f"ORDER:{order.get('code') or 'UNKNOWN'}"
                    group_label = order.get("code") or "Unknown Order"
                else:
                    group_key = f"PO:{reference}"
                    group_label = reference
            else:
                group_key = f"ORDER:{order.get('code') or 'UNKNOWN'}"
                group_label = order.get("code") or "Unknown Order"

            if group_key not in grouped_candidates:
                grouped_candidates[group_key] = {
                    "group_key": group_key,
                    "group_label": group_label,
                    "orders": [],
                    "lines": [],
                    "shipping": 0.0,
                    "subtotal": 0.0,
                    "total": 0.0,
                    "currency": order.get("currency") or "$"
                }

            grouped_entry = grouped_candidates[group_key]
            grouped_entry["orders"].append(order)
            grouped_entry["shipping"] += _to_number(invoice.get("shipping", 0), 0)

            for line in invoice.get("lines", []):
                enriched_line = {
                    **line,
                    "order_code": order.get("code")
                }
                grouped_entry["lines"].append(enriched_line)
                grouped_entry["subtotal"] += _to_number(enriched_line.get("line_total", 0), 0)

        normalized_candidates = []
        for grouped_entry in grouped_candidates.values():
            grouped_entry["total"] = grouped_entry["subtotal"] + grouped_entry["shipping"]
            primary_order = grouped_entry["orders"][0] if grouped_entry["orders"] else {}
            if generation_mode == "po" and len(grouped_entry["orders"]) > 1:
                primary_order = {
                    **primary_order,
                    "code": grouped_entry["group_label"],
                    "customer_name": "Multiple Customers" if len({(o or {}).get('customer_name') for o in grouped_entry["orders"]}) > 1 else primary_order.get("customer_name"),
                    "job_number": "Multiple",
                    "reference": grouped_entry["group_label"]
                }

            normalized_candidates.append({
                "group_key": grouped_entry["group_key"],
                "group_label": grouped_entry["group_label"],
                "orders": grouped_entry["orders"],
                "order": primary_order,
                "invoice": {
                    "lines": grouped_entry["lines"],
                    "shipping": grouped_entry["shipping"],
                    "subtotal": grouped_entry["subtotal"],
                    "total": grouped_entry["total"]
                }
            })

        candidates = normalized_candidates

        created = []
        skipped = []

        now = datetime.utcnow()
        timestamp = now.strftime("%Y%m%d%H%M%S")

        for candidate in candidates:
            order = candidate.get("order", {})
            invoice = candidate.get("invoice", {})
            group_key = candidate.get("group_key") or f"ORDER:{order.get('code') or 'UNKNOWN'}"
            draft_key = f"{group_key}:{round(_to_number(invoice.get('total', 0), 0), 2)}"

            if draft_key in _generated_invoice_keys:
                skipped.append({
                    "order_code": order.get("code") or candidate.get("group_label") or "N/A",
                    "reason": "Draft already generated"
                })
                continue

            safe_group_id = group_key.replace(":", "-").replace(" ", "_")
            draft_id = f"LOCAL-{safe_group_id}-{timestamp}-{len(_generated_invoice_drafts) + 1}"
            draft = {
                "draft_id": draft_id,
                "draft_key": draft_key,
                "created_at": now.isoformat() + "Z",
                "status": "local_draft",
                "source": "not_invoiced",
                "generation_mode": generation_mode,
                "group_key": group_key,
                "group_label": candidate.get("group_label") or order.get("code") or "N/A",
                "orders": candidate.get("orders", [order]),
                "order": order,
                "invoice": {
                    **invoice,
                    "draft_code": draft_id
                }
            }

            _generated_invoice_drafts.append(draft)
            _generated_invoice_keys.add(draft_key)
            created.append(draft)

        return {
            "created_count": len(created),
            "skipped_count": len(skipped),
            "created": created,
            "skipped": skipped
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create invoice drafts: {exc}")


@router.get("/generator/drafts")
def list_generated_invoice_drafts():
    return {
        "drafts": list(reversed(_generated_invoice_drafts)),
        "count": len(_generated_invoice_drafts)
    }


@router.delete("/generator/drafts/{draft_id}")
def delete_generated_invoice_draft(draft_id: str):
    for index, draft in enumerate(_generated_invoice_drafts):
        if draft.get("draft_id") != draft_id:
            continue

        draft_key = draft.get("draft_key")
        if not draft_key:
            order_code = (draft.get("order") or {}).get("code") or "UNKNOWN"
            total = _to_number((draft.get("invoice") or {}).get("total"), 0)
            draft_key = f"{order_code}:{round(total, 2)}"

        if draft_key in _generated_invoice_keys:
            _generated_invoice_keys.remove(draft_key)

        deleted = _generated_invoice_drafts.pop(index)
        return {
            "deleted": True,
            "draft_id": draft_id,
            "remaining_count": len(_generated_invoice_drafts),
            "draft": deleted
        }

    raise HTTPException(status_code=404, detail="Draft not found")


@router.get("/")
def list_invoices(status: Optional[int] = None, limit: Optional[int] = None):
    """List sales invoices from MRPeasy API."""
    try:
        filters = {}
        if status is not None:
            filters["status"] = status
        if limit is not None:
            filters["limit"] = limit
        invoices = mrpeasy_client.get_invoices(filters)
        invoices = _enrich_invoices_with_reference(invoices or [])
        return invoices or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch invoices: {exc}")


@router.get("/unsubmitted")
def get_unsubmitted_invoices():
    """
    Fetch draft/unsubmitted invoices from MRPeasy with full details including line items.
    Status 10 = Draft (not submitted)
    """
    try:
        # Fetch invoices with status 10 (Draft/Not submitted)
        filters = {"status": 10, "limit": 1000}
        invoices = mrpeasy_client.get_invoices(filters)
        
        if not invoices:
            return {
                "invoices": [],
                "total_count": 0,
                "total_value": 0,
                "currency": "$"
            }
        
        # Fetch full details for each invoice to get line items
        detailed_invoices = []
        for inv in invoices:
            invoice_id = inv.get("invoice_id")
            if invoice_id:
                try:
                    invoice_detail = mrpeasy_client.get_invoice(invoice_id)
                    if invoice_detail:
                        detailed_invoices.append(invoice_detail)
                except Exception as e:
                    print(f"Error fetching invoice {invoice_id}: {e}")
                    # Include basic invoice if detail fetch fails
                    detailed_invoices.append(inv)
        
        detailed_invoices = _enrich_invoices_with_reference(detailed_invoices)

        total_value = sum(inv.get("total_price", 0) or 0 for inv in detailed_invoices)
        currency = detailed_invoices[0].get("currency", "$") if detailed_invoices else "$"
        
        return {
            "invoices": detailed_invoices,
            "total_count": len(detailed_invoices),
            "total_value": total_value,
            "currency": currency
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch unsubmitted invoices: {exc}")


@router.get("/{invoice_id}")
def get_invoice(invoice_id: int):
    """Fetch a single sales invoice by ID from MRPeasy API."""
    try:
        invoice = mrpeasy_client.get_invoice(invoice_id)
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")
        return invoice
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch invoice: {exc}")


@router.get("/shipped-uninvoiced/items")
def get_shipped_uninvoiced_items():
    """
    Find customer orders with invoicing discrepancies.
    
    Detects:
    - Under-invoiced items: shipped qty > invoiced qty (missing invoices)
    - Over-invoiced items: invoiced qty > shipped qty (invoice ahead of shipment)
    
    Logic:
    1. Filter orders with invoice_status = 10 (Not invoiced) or 20 (Partially invoiced)
    2. For each order, identify items with shipped qty > 0
    3. Sum all invoice line items for each item across multiple invoices (EXCLUDING cancelled invoices)
    4. Calculate discrepancy: shipped_qty - invoiced_qty
    5. Flag any non-zero discrepancies for review
    6. Ignore items with $0 selling cost (tracked separately)
    """
    try:
        # Fetch all customer orders
        all_orders = mrpeasy_client.get_customer_orders()
        
        if not all_orders:
            return {
                "shipped_uninvoiced_orders": [],
                "total_orders": 0,
                "total_uninvoiced_items": 0
            }
        
        # Fetch all invoices and shipments
        all_invoices = mrpeasy_client.get_invoices()
        all_shipments = mrpeasy_client.get_shipments() or []
        
        # Build shipment_map to track which items are in which shipments
        # This allows us to match invoices to specific shipment instances
        shipment_map = {}  # {cust_ord_id: {shipment_code: {match_keys}}}
        if all_shipments:
            for shipment in all_shipments:
                shipment_code = shipment.get('code') or 'N/A'
                customer_order_code = shipment.get('customer_order_code')
                
                # Get order ID from shipment
                cust_ord_id = None
                direct_id = shipment.get('customer_order_id')
                if direct_id is not None:
                    cust_ord_id = direct_id
                
                direct_cust_ord_id = shipment.get('cust_ord_id')
                if direct_cust_ord_id is not None and cust_ord_id is None:
                    cust_ord_id = direct_cust_ord_id
                
                for linked in shipment.get('orders', []) or []:
                    linked_id = linked.get('customer_order_id')
                    if linked_id is not None and cust_ord_id is None:
                        cust_ord_id = linked_id
                
                if not cust_ord_id:
                    continue
                
                if cust_ord_id not in shipment_map:
                    shipment_map[cust_ord_id] = {}
                
                shipment_map[cust_ord_id][shipment_code] = set()
                
                # Track match keys for items in this shipment
                for shipment_product in shipment.get('products', []) or []:
                    match_key = _build_invoice_match_key(shipment_product)
                    if match_key:
                        shipment_map[cust_ord_id][shipment_code].add(match_key)
        
        # Helper function: Match invoices to their likely shipments
        # This helps ensure invoices are only attributed to shipment instances they actually cover
        def _match_invoice_to_shipments(invoice, cust_ord_id):
            """
            Determine which shipments an invoice likely applies to based on its item content.
            Returns list of shipment codes that likely contain these items.
            """
            likely_shipments = []
            if cust_ord_id not in shipment_map:
                return likely_shipments
            
            order_shipments = shipment_map[cust_ord_id]
            invoice_match_keys = set()
            
            # Get all match_keys in this invoice
            for product in invoice.get('products', []) or []:
                match_key = _build_invoice_match_key(product)
                if match_key and str(product.get('item_code', '')).lower() != 'shipping':
                    invoice_match_keys.add(match_key)
            
            if not invoice_match_keys:
                # No matchable items in invoice
                return list(order_shipments.keys()) if order_shipments else []
            
            # Find shipments that contain these match_keys
            for shipment_code, shipment_match_keys in order_shipments.items():
                if invoice_match_keys.intersection(shipment_match_keys):
                    likely_shipments.append(shipment_code)
            
            return likely_shipments if likely_shipments else list(order_shipments.keys())
        
        # Build invoice pools by order and match key.
        # Match key uses article_id when available, then item_code.
        # Also track shipping costs per order.
        # NEW: Track which shipments each invoice product likely belongs to
        invoice_items_map = {}
        invoice_shipments_map = {}  # {cust_ord_id: {invoice_code: {match_keys}}}
        invoice_shipment_links = {}  # {cust_ord_id: {invoice_code: [shipment_codes]}} - which shipments each invoice covers
        shipping_costs_map = {}  # {cust_ord_id: total_shipping}
        if all_invoices:
            for invoice in all_invoices:
                invoice_status = str(invoice.get('status', ''))
                
                # IMPROVEMENT 1: Skip cancelled invoices (status 50) only
                # Include dummy (10), unpaid (20), paid partially (30), and paid (40)
                if invoice_status == '50':
                    continue
                
                cust_ord_id = invoice.get('cust_ord_id')
                invoice_code = invoice.get('code', f"INV-{invoice.get('invoice_id')}")
                
                if not cust_ord_id:
                    continue
                
                if cust_ord_id not in invoice_items_map:
                    invoice_items_map[cust_ord_id] = {}
                
                if cust_ord_id not in invoice_shipments_map:
                    invoice_shipments_map[cust_ord_id] = {}
                
                if cust_ord_id not in invoice_shipment_links:
                    invoice_shipment_links[cust_ord_id] = {}
                
                if cust_ord_id not in shipping_costs_map:
                    shipping_costs_map[cust_ord_id] = 0
                
                # Track which shipments this invoice likely applies to
                likely_shipments = _match_invoice_to_shipments(invoice, cust_ord_id)
                if likely_shipments:
                    invoice_shipment_links[cust_ord_id][invoice_code] = likely_shipments
                
                # Track which match_keys are in this invoice
                invoice_shipments_map[cust_ord_id][invoice_code] = set()
                
                # Get products from invoice
                invoice_products = invoice.get('products', [])
                for product in invoice_products:
                    item_code = product.get('item_code')
                    quantity = _to_number(product.get('quantity', 0), 0)
                    
                    # Calculate shipping costs (Shipping line items)
                    if item_code and str(item_code).lower() == 'shipping':
                        shipping_price = _to_number(product.get('total_price', 0), 0)
                        shipping_costs_map[cust_ord_id] += shipping_price
                        continue  # Don't add shipping to invoice_items_map

                    if quantity <= 0:
                        continue

                    match_key = _build_invoice_match_key(product)
                    if not match_key:
                        continue
                    
                    # Track this match_key as being in this invoice
                    invoice_shipments_map[cust_ord_id][invoice_code].add(match_key)

                    if match_key not in invoice_items_map[cust_ord_id]:
                        invoice_items_map[cust_ord_id][match_key] = {
                            'total_qty': 0,
                            'invoice_codes': [],
                            'by_invoice': {}  # Track quantity per invoice for this match_key
                        }

                    invoice_items_map[cust_ord_id][match_key]['total_qty'] += quantity
                    if invoice_code not in invoice_items_map[cust_ord_id][match_key]['invoice_codes']:
                        invoice_items_map[cust_ord_id][match_key]['invoice_codes'].append(invoice_code)
                    
                    # NEW: Track quantity by invoice for this match_key
                    if invoice_code not in invoice_items_map[cust_ord_id][match_key]['by_invoice']:
                        invoice_items_map[cust_ord_id][match_key]['by_invoice'][invoice_code] = 0
                    invoice_items_map[cust_ord_id][match_key]['by_invoice'][invoice_code] += quantity
        
        shipped_uninvoiced_orders = []
        ignored_items_orders = []  # IMPROVEMENT 2: Track $0 items separately
        total_discrepancy_items = 0
        
        # Process orders with invoice_status '10' or '20'
        for order in all_orders:
            invoice_status = order.get('invoice_status')
            
            # Only process not invoiced ('10') or partially invoiced ('20') orders
            # Note: invoice_status comes as string from API
            if str(invoice_status) not in ['10', '20']:
                continue
            
            cust_ord_id = order.get('cust_ord_id')
            products = order.get('products', [])
            
            shipped_lines = []
            lines_by_match_key = {}

            for line_index, product in enumerate(products):
                shipped_qty = _to_number(product.get('shipped', 0), 0)
                if shipped_qty <= 0:
                    continue

                match_key = _build_invoice_match_key(product)
                if not match_key:
                    continue

                order_line = str(product.get('ord') or product.get('order_line') or '1')
                delivery_date = _format_epoch_to_iso(product.get('delivery_date')) or 'N/A'
                line_key = _build_discrepancy_line_key(order.get('code'), product, line_index)

                line_data = {
                    'line_key': line_key,
                    'line_index': line_index,
                    'order_line': order_line,
                    'delivery_date': delivery_date,
                    'article_id': product.get('article_id'),
                    'item_code': product.get('item_code'),
                    'item_title': product.get('item_title'),
                    'shipped_quantity': shipped_qty,
                    'order_quantity': _to_number(product.get('quantity', 0), 0),
                    'item_price': _to_number(product.get('item_price', 0), 0),
                    'total_price': _to_number(product.get('total_price', 0), 0),
                    'match_key': match_key,
                    'invoiced_quantity': 0,
                    'invoice_codes': []
                }

                shipped_lines.append(line_data)
                lines_by_match_key.setdefault(match_key, []).append(len(shipped_lines) - 1)

            def _line_sort_key(line):
                delivery = line.get('delivery_date') or '9999-12-31'
                if delivery == 'N/A':
                    delivery = '9999-12-31'
                return (delivery, line.get('line_index', 0))

            # NEW: Smarter invoice allocation that matches invoices to specific shipment instances
            # Instead of allocating invoices in bulk by match_key, now allocate per line_key
            # to properly handle multiple shipments of the same item
            for match_key, indexes in lines_by_match_key.items():
                # Get available invoices for this match_key
                available_invoice_qty = 0
                all_invoice_codes = []
                by_invoice_qty = {}  # {invoice_code: qty}
                if cust_ord_id in invoice_items_map and match_key in invoice_items_map[cust_ord_id]:
                    invoice_data = invoice_items_map[cust_ord_id][match_key]
                    available_invoice_qty = invoice_data.get('total_qty', 0)
                    all_invoice_codes = invoice_data.get('invoice_codes', [])
                    by_invoice_qty = invoice_data.get('by_invoice', {}).copy()

                # Sort lines by delivery_date to allocate invoices chronologically
                sorted_indexes = sorted(indexes, key=lambda i: _line_sort_key(shipped_lines[i]))
                
                # NEW: Try to match invoices to specific shipment instances
                for idx in sorted_indexes:
                    line = shipped_lines[idx]
                    shipped_qty = line['shipped_quantity']
                    delivery_date = line.get('delivery_date')
                    
                    # Try to find invoices that match this specific shipment instance
                    # Prefer invoices with the same or close delivery date
                    matched_invoices = []
                    
                    # First, try to match by delivery date proximity
                    if delivery_date and delivery_date != 'N/A':
                        for invoice_code in all_invoice_codes:
                            if by_invoice_qty.get(invoice_code, 0) > 0:
                                # This invoice has available quantity
                                # For now, allocate to first matching invoice(s)
                                matched_invoices.append(invoice_code)
                    
                    if not matched_invoices:
                        # Fall back to using first available invoice
                        matched_invoices = [ic for ic in all_invoice_codes if by_invoice_qty.get(ic, 0) > 0]
                    
                    # Allocate from matched invoices
                    allocated_qty = 0
                    used_invoices = []
                    for invoice_code in matched_invoices:
                        if allocated_qty >= shipped_qty:
                            break
                        available = by_invoice_qty.get(invoice_code, 0)
                        to_allocate = min(shipped_qty - allocated_qty, available)
                        if to_allocate > 0:
                            by_invoice_qty[invoice_code] -= to_allocate
                            allocated_qty += to_allocate
                            if invoice_code not in used_invoices:
                                used_invoices.append(invoice_code)
                    
                    line['invoiced_quantity'] = allocated_qty
                    # IMPROVEMENT: Only include invoices that actually have allocated quantity for this line
                    # Don't include invoices that were fully consumed by earlier lines
                    line['invoice_codes'] = used_invoices
                    available_invoice_qty -= allocated_qty
            
            discrepancy_items = []
            ignored_items = []  # Items with $0 selling cost
            
            # Check each shipped line for invoicing discrepancies (line-level)
            for line in shipped_lines:
                item_code = line['item_code']
                shipped_qty = line['shipped_quantity']
                invoiced_qty = line['invoiced_quantity']
                invoice_codes = line['invoice_codes']
                item_price = line['item_price']
                total_price = line['total_price']
                
                # Ignore items only when they truly carry no selling price.
                if _has_zero_selling_price(item_price, total_price, shipped_qty):
                    discrepancy = shipped_qty - invoiced_qty

                    # Track in ignored items if there's a discrepancy
                    if discrepancy != 0:
                        ignored_items.append({
                            'line_key': line['line_key'],
                            'line_index': line['line_index'],
                            'order_line': line['order_line'],
                            'delivery_date': line['delivery_date'],
                            'article_id': line['article_id'],
                            'item_code': item_code,
                            'item_title': line['item_title'],
                            'order_quantity': line['order_quantity'],
                            'shipped_quantity': shipped_qty,
                            'invoiced_quantity': invoiced_qty,
                            'discrepancy': discrepancy,
                            'item_price': 0,
                            'total_price': 0,
                            'reason': 'Zero selling cost',
                            'invoice_codes': invoice_codes
                        })
                    continue  # Skip from main discrepancy logic

                # Calculate discrepancy: shipped - invoiced
                discrepancy = shipped_qty - invoiced_qty

                # Flag ANY discrepancy (positive or negative)
                if discrepancy != 0:
                    # Determine discrepancy type - 3 categories
                    if invoiced_qty == 0:
                        # No invoices created yet
                        discrepancy_type = "not_invoiced"
                        discrepancy_description = f"Shipped {shipped_qty} units but no invoice created"
                    elif discrepancy > 0:
                        # Partial invoices exist but shipment exceeds invoices
                        discrepancy_type = "under_invoiced"
                        discrepancy_description = f"Shipped {shipped_qty} units but only {invoiced_qty} invoiced (missing {abs(discrepancy)} units)"
                    else:
                        # Invoiced more than shipped (over-invoiced)
                        discrepancy_type = "over_invoiced"
                        discrepancy_description = f"Invoice exceeds shipment by {abs(discrepancy)} units (shipped {shipped_qty}, invoiced {invoiced_qty})"
                    
                    discrepancy_items.append({
                        'line_key': line['line_key'],
                        'line_index': line['line_index'],
                        'order_line': line['order_line'],
                        'delivery_date': line['delivery_date'],
                        'article_id': line['article_id'],
                        'item_code': item_code,
                        'item_title': line['item_title'],
                        'order_quantity': line['order_quantity'],
                        'shipped_quantity': shipped_qty,
                        'invoiced_quantity': invoiced_qty,
                        'discrepancy': discrepancy,
                        'discrepancy_type': discrepancy_type,
                        'alert': discrepancy_description,
                        'fulfillment_status': 'partial' if shipped_qty < line['order_quantity'] else 'complete',
                        'invoice_codes': invoice_codes,  # IMPROVEMENT 4: Include invoice codes
                        'item_price': item_price,
                        'total_price': total_price
                    })
                    total_discrepancy_items += 1
            
            # If order has discrepancy items, add to results
            if discrepancy_items:
                # Separate by discrepancy type for summary (3 categories)
                not_invoiced = [item for item in discrepancy_items if item['discrepancy_type'] == 'not_invoiced']
                under_invoiced = [item for item in discrepancy_items if item['discrepancy_type'] == 'under_invoiced']
                over_invoiced = [item for item in discrepancy_items if item['discrepancy_type'] == 'over_invoiced']
                
                shipped_uninvoiced_orders.append({
                    'order': {
                        'cust_ord_id': cust_ord_id,
                        'code': order.get('code'),
                        'reference': order.get('reference'),
                        'customer_name': order.get('customer_name'),
                        'status': order.get('status_txt'),
                        'invoice_status': invoice_status,
                        'invoice_status_text': 'Not Invoiced' if invoice_status == '10' else 'Partially Invoiced',
                        'total_price': order.get('total_price'),
                        'currency': order.get('currency'),
                        'job_number': _extract_job_number(order),
                        'shipping_cost': shipping_costs_map.get(cust_ord_id, 0)  # NEW: Shipping cost
                    },
                    'discrepancy_items': discrepancy_items,
                    'total_discrepancy_items': len(discrepancy_items),
                    'not_invoiced_items': len(not_invoiced),
                    'under_invoiced_items': len(under_invoiced),
                    'over_invoiced_items': len(over_invoiced)
                })
            
            # IMPROVEMENT 2: Track orders with ignored items
            if ignored_items:
                ignored_items_orders.append({
                    'order': {
                        'cust_ord_id': cust_ord_id,
                        'code': order.get('code'),
                        'reference': order.get('reference'),
                        'customer_name': order.get('customer_name'),
                        'status': order.get('status_txt'),
                        'invoice_status': invoice_status,
                        'invoice_status_text': 'Not Invoiced' if invoice_status == '10' else 'Partially Invoiced',
                        'total_price': order.get('total_price'),
                        'currency': order.get('currency'),
                        'job_number': _extract_job_number(order),
                        'shipping_cost': shipping_costs_map.get(cust_ord_id, 0)  # NEW: Shipping cost
                    },
                    'ignored_items': ignored_items,
                    'total_ignored_items': len(ignored_items)
                })
        
        # Categorize all orders by discrepancy type
        not_invoiced_orders = []
        under_invoiced_orders = []
        over_invoiced_orders = []
        
        for order_data in shipped_uninvoiced_orders:
            if order_data['not_invoiced_items'] > 0:
                not_invoiced_orders.append(order_data)
            if order_data['under_invoiced_items'] > 0:
                under_invoiced_orders.append(order_data)
            if order_data['over_invoiced_items'] > 0:
                over_invoiced_orders.append(order_data)
        
        return {
            "not_invoiced": {
                "orders": not_invoiced_orders,
                "total_orders": len(not_invoiced_orders),
                "description": "Items shipped but no invoice created (invoiced quantity = 0)"
            },
            "under_invoiced": {
                "orders": under_invoiced_orders,
                "total_orders": len(under_invoiced_orders),
                "description": "Items with partial invoices (shipped > invoiced, but invoiced > 0)"
            },
            "over_invoiced": {
                "orders": over_invoiced_orders,
                "total_orders": len(over_invoiced_orders),
                "description": "Items invoiced more than shipped (invoiced > shipped)"
            },
            "ignored_items": {
                "orders": ignored_items_orders,
                "total_orders": len(ignored_items_orders),
                "description": "Items with $0 selling cost (excluded from invoicing analysis)"
            },
            "summary": {
                "total_orders_with_discrepancies": len(shipped_uninvoiced_orders),
                "total_items_with_discrepancies": total_discrepancy_items,
                "not_invoiced_count": len(not_invoiced_orders),
                "under_invoiced_count": len(under_invoiced_orders),
                "over_invoiced_count": len(over_invoiced_orders),
                "ignored_items_count": len(ignored_items_orders)
            }
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch shipped uninvoiced items: {str(e)}"
        )

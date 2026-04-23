from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from app.services.mrpeasy_client import mrpeasy_client

router = APIRouter(prefix="/api/invoicing", tags=["invoicing"])

_generated_invoice_drafts = []
_generated_invoice_keys = set()


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

        order_ids = set()
        direct_id = shipment.get("customer_order_id")
        if direct_id is not None:
            order_ids.add(direct_id)

        for linked in shipment.get("orders", []) or []:
            linked_id = linked.get("customer_order_id")
            if linked_id is not None:
                order_ids.add(linked_id)

        for order_id in order_ids:
            if order_id not in shipment_map:
                shipment_map[order_id] = {
                    "shipment_codes": [],
                    "delivery_dates": []
                }

            if shipment_code not in shipment_map[order_id]["shipment_codes"]:
                shipment_map[order_id]["shipment_codes"].append(shipment_code)
            if delivery_date and delivery_date not in shipment_map[order_id]["delivery_dates"]:
                shipment_map[order_id]["delivery_dates"].append(delivery_date)

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
                "shipment_number": ", ".join(shipment_codes) if shipment_codes else "N/A",
                "delivery_date": item.get("delivery_date") or ", ".join(delivery_dates) if delivery_dates else "N/A"
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
        
        # Fetch all invoices
        all_invoices = mrpeasy_client.get_invoices()
        
        # Build invoice pools by order and match key.
        # Match key uses article_id when available, then item_code.
        # Also track shipping costs per order.
        invoice_items_map = {}
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
                
                if cust_ord_id not in shipping_costs_map:
                    shipping_costs_map[cust_ord_id] = 0
                
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

                    if match_key not in invoice_items_map[cust_ord_id]:
                        invoice_items_map[cust_ord_id][match_key] = {
                            'total_qty': 0,
                            'invoice_codes': []
                        }

                    invoice_items_map[cust_ord_id][match_key]['total_qty'] += quantity
                    if invoice_code not in invoice_items_map[cust_ord_id][match_key]['invoice_codes']:
                        invoice_items_map[cust_ord_id][match_key]['invoice_codes'].append(invoice_code)
        
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

            for match_key, indexes in lines_by_match_key.items():
                available_invoice_qty = 0
                invoice_codes = []
                if cust_ord_id in invoice_items_map and match_key in invoice_items_map[cust_ord_id]:
                    available_invoice_qty = invoice_items_map[cust_ord_id][match_key]['total_qty']
                    invoice_codes = invoice_items_map[cust_ord_id][match_key]['invoice_codes']

                for idx in sorted(indexes, key=lambda i: _line_sort_key(shipped_lines[i])):
                    line = shipped_lines[idx]
                    allocated_qty = min(line['shipped_quantity'], available_invoice_qty)
                    line['invoiced_quantity'] = allocated_qty
                    line['invoice_codes'] = invoice_codes
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

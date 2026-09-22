"""
Label generation routes
"""
from fastapi import APIRouter, HTTPException, Depends, Body, UploadFile, File
from typing import Literal, Dict, List, Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from datetime import datetime
import json
import io
import re
import pandas as pd
from app.services.mrpeasy_client import mrpeasy_client
from app.config.database import get_db
from app.dependencies import require_module, require_permission
from app.models import ShipmentBox, PackSize, Label, User, PalletWeight

class FinalizeShipmentRequest(BaseModel):
    pallet_number: Optional[str] = None
    product_configs: Dict = {}


class BulkFinalizeShipmentRequest(FinalizeShipmentRequest):
    shipment_code: str


class BulkFinalizeRequest(BaseModel):
    shipments: List[BulkFinalizeShipmentRequest]


class PastePackSizesRequest(BaseModel):
    text: str


class ProcessPackSizesRequest(BaseModel):
    text: str
    confirm_blank_duplicates: bool = False


class UpdatePackSizeRequest(BaseModel):
    item_code: str
    pack_size: int


class PastePalletNumbersRequest(BaseModel):
    text: str


class UpdatePalletNumbersRequest(BaseModel):
    # Keyed by "item_code:order_line" -> pallet number (blank string clears it)
    pallet_numbers: Dict[str, str] = {}


class UpdatePalletWeightsRequest(BaseModel):
    # Keyed by pallet number -> weight
    weights: Dict[str, Optional[float]] = {}


router = APIRouter(
    prefix="/api/labels",
    tags=["labels"],
    dependencies=[Depends(require_module("batch_labels"))]
)


def _normalize_item_code(value) -> str:
    """
    Normalize item code variants so e.g. "15437-NUTS", "15437-NUT", "15437-nut"
    and "15437 - NUTS" all match the same underlying item code.
    """
    text = str(value or '').strip().upper()
    text = re.sub(r'\s+', '', text)  # "15437 - NUT" -> "15437-NUT"
    text = re.sub(r'NUTS$', 'NUT', text)  # treat plural NUTS suffix as NUT
    return text


def _serialize_finalized_labels(boxes: List[ShipmentBox]) -> List[Dict]:
    total_boxes_by_item = {}
    for box in boxes:
        group_key = (box.item_code, box.order_line)
        total_boxes_by_item[group_key] = total_boxes_by_item.get(group_key, 0) + 1

    labels = []
    for box in boxes:
        lot_codes = []
        if box.lot_codes:
            try:
                parsed_lot_codes = json.loads(box.lot_codes)
                lot_codes = parsed_lot_codes if isinstance(parsed_lot_codes, list) else [parsed_lot_codes]
            except (TypeError, json.JSONDecodeError):
                lot_codes = [box.lot_codes]

        group_key = (box.item_code, box.order_line)
        labels.append({
            'shipment_code': box.shipment_code,
            'customer_order': box.customer_order_code,
            'customer_name': box.customer_name,
            'reference': box.po_number,
            'job_number': box.job_number,
            'item_code': box.item_code,
            'item_title': box.item_title,
            'order_line': box.order_line,
            'lot_code': ', '.join(str(code) for code in lot_codes if code),
            'box_number': box.box_number,
            'total_boxes': total_boxes_by_item[group_key],
            'quantity_in_box': box.quantity_in_box,
            'total_quantity': box.total_quantity,
            'label_type': 'individual',
            'pack_size': box.pack_size,
            'finalized_at': box.finalized_at.isoformat() if box.finalized_at else None
        })

    return labels


def _extract_pallet_numbers_from_rows(rows: List[list]):
    """
    Given rows of raw cell values (first row may be a header), detect
    "item"/"pallet"/"weight" header columns (case-insensitive) or fall back to
    column 1 = item #, column 2 = pallet number, column 3 = weight (optional).
    Blank pallet numbers are skipped (pallet assignment is optional per item).

    The same pallet number can repeat across rows (multiple items on one
    pallet); weight only needs to be filled in on one of those rows. If two
    rows for the same pallet number have different non-blank weights, that's
    reported as a conflict instead of guessing which one is correct.

    Returns (pallet_numbers, pallet_weights, weight_conflicts):
      - pallet_numbers: {item_code: pallet_number}
      - pallet_weights: {pallet_number: weight} (only for pallets with a single, consistent weight)
      - weight_conflicts: [{pallet_number, weights: [...]}] for pallets with conflicting weights
    """
    if not rows:
        return {}, {}, []

    item_col = 0
    pallet_col = 1
    weight_col = 2

    header_row = rows[0]
    item_col_match = None
    pallet_col_match = None
    weight_col_match = None
    for col_idx, header_value in enumerate(header_row):
        if header_value is None or pd.isna(header_value):
            continue
        header_text = str(header_value).strip().lower()
        if not header_text:
            continue
        if item_col_match is None and 'item' in header_text:
            item_col_match = col_idx
        if pallet_col_match is None and 'pallet' in header_text:
            pallet_col_match = col_idx
        if weight_col_match is None and 'weight' in header_text:
            weight_col_match = col_idx

    data_rows = rows
    if item_col_match is not None and pallet_col_match is not None:
        item_col = item_col_match
        pallet_col = pallet_col_match
        weight_col = weight_col_match if weight_col_match is not None else weight_col
        data_rows = rows[1:]

    pallet_numbers: Dict[str, str] = {}
    weight_occurrences: Dict[str, List[float]] = {}

    for row in data_rows:
        if len(row) <= max(item_col, pallet_col):
            continue
        item_code_raw = row[item_col]
        pallet_raw = row[pallet_col]

        if item_code_raw is None or pd.isna(item_code_raw):
            continue
        item_code = str(item_code_raw).strip()
        if not item_code:
            continue

        if pallet_raw is None or pd.isna(pallet_raw):
            continue
        pallet_number = str(pallet_raw).strip()
        if not pallet_number:
            continue

        pallet_numbers[item_code] = pallet_number

        if weight_col is not None and len(row) > weight_col:
            weight_raw = row[weight_col]
            if weight_raw is not None and not pd.isna(weight_raw):
                weight_text = str(weight_raw).strip()
                if weight_text:
                    # Accept "2,169.00" style thousands separators (and stray currency symbols).
                    cleaned_weight_text = re.sub(r'[,$\s]', '', weight_text)
                    try:
                        weight_value = float(cleaned_weight_text)
                        weight_occurrences.setdefault(pallet_number, []).append(weight_value)
                    except (TypeError, ValueError):
                        pass

    pallet_weights: Dict[str, float] = {}
    weight_conflicts = []
    for pallet_number, weights in weight_occurrences.items():
        unique_weights = sorted(set(weights))
        if len(unique_weights) > 1:
            weight_conflicts.append({'pallet_number': pallet_number, 'weights': unique_weights})
        elif unique_weights:
            pallet_weights[pallet_number] = unique_weights[0]

    return pallet_numbers, pallet_weights, weight_conflicts


def _extract_pack_sizes_from_rows(rows: List[list]) -> Dict[str, int]:
    """
    Given rows of raw cell values (first row may be a header), detect
    "item"/"pack" header columns (case-insensitive) or fall back to
    column 1 = item #, column 2 = pack size. Blank pack sizes are skipped
    so the caller keeps its own default (typically the full order quantity).
    """
    if not rows:
        return {}

    item_col = 0
    pack_col = 1

    header_row = rows[0]
    item_col_match = None
    pack_col_match = None
    for col_idx, header_value in enumerate(header_row):
        if header_value is None or pd.isna(header_value):
            continue
        header_text = str(header_value).strip().lower()
        if not header_text:
            continue
        if item_col_match is None and 'item' in header_text:
            item_col_match = col_idx
        if pack_col_match is None and 'pack' in header_text:
            pack_col_match = col_idx

    data_rows = rows
    if item_col_match is not None and pack_col_match is not None:
        item_col = item_col_match
        pack_col = pack_col_match
        data_rows = rows[1:]

    pack_sizes: Dict[str, int] = {}
    for row in data_rows:
        if len(row) <= max(item_col, pack_col):
            continue
        item_code_raw = row[item_col]
        pack_size_raw = row[pack_col]

        if item_code_raw is None or pd.isna(item_code_raw):
            continue
        item_code_raw = str(item_code_raw).strip()
        if not item_code_raw:
            continue

        normalized_code = _normalize_item_code(item_code_raw)
        if not normalized_code:
            continue

        if pack_size_raw is None or pd.isna(pack_size_raw):
            continue  # blank pack size -> caller keeps its own default (order qty)
        pack_size_text = str(pack_size_raw).strip()
        if not pack_size_text:
            continue

        try:
            pack_size = int(float(pack_size_text))
        except (ValueError, TypeError):
            continue

        if pack_size <= 0:
            continue

        pack_sizes[normalized_code] = pack_size

    return pack_sizes


def _parse_pack_size_processor_text(text: str):
    occurrences = {}
    invalid_rows = []
    tab_item_col = None
    tab_pack_col = None

    for row_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue

        if '\t' in raw_line:
            cells = [cell.strip() for cell in raw_line.split('\t')]
            if tab_item_col is None and tab_pack_col is None:
                lowered_cells = [cell.lower() for cell in cells]
                detected_item_col = next(
                    (index for index, value in enumerate(lowered_cells) if 'item' in value),
                    None
                )
                detected_pack_col = next(
                    (index for index, value in enumerate(lowered_cells) if 'pack' in value),
                    None
                )
                if detected_item_col is not None and detected_pack_col is not None:
                    tab_item_col = detected_item_col
                    tab_pack_col = detected_pack_col
                    continue

            if tab_item_col is not None and tab_pack_col is not None:
                item_code_raw = cells[tab_item_col] if len(cells) > tab_item_col else ''
                pack_size_raw = cells[tab_pack_col] if len(cells) > tab_pack_col else ''
            else:
                item_index = next((index for index, value in enumerate(cells) if value), 0)
                item_code_raw = cells[item_index] if cells else ''
                following_cells = cells[item_index + 1:]
                pack_size_raw = next(
                    (
                        value for value in following_cells
                        if value and re.fullmatch(r'\d+(?:\.0+)?', value)
                    ),
                    following_cells[0] if following_cells else ''
                )
        elif ',' in raw_line:
            item_code_raw, pack_size_raw = (part.strip() for part in raw_line.split(',', 1))
        else:
            parts = raw_line.strip().rsplit(None, 1)
            item_code_raw = parts[0]
            pack_size_raw = parts[1] if len(parts) > 1 else ''

        if row_number == 1 and 'item' in item_code_raw.lower() and 'pack' in pack_size_raw.lower():
            continue

        item_code = _normalize_item_code(item_code_raw)
        if not item_code:
            invalid_rows.append({'row': row_number, 'reason': 'Missing item number'})
            continue

        pack_size = None
        if pack_size_raw:
            try:
                numeric_size = float(pack_size_raw)
                pack_size = int(numeric_size)
                if numeric_size != pack_size or pack_size <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                invalid_rows.append({
                    'row': row_number,
                    'item_code': item_code,
                    'reason': 'Pack size must be a positive whole number'
                })
                continue

        occurrences.setdefault(item_code, []).append({
            'row': row_number,
            'pack_size': pack_size
        })

    valid_pack_sizes = {}
    conflicts = []
    blank_warnings = []

    for item_code, entries in occurrences.items():
        sizes = sorted({entry['pack_size'] for entry in entries if entry['pack_size'] is not None})
        blank_rows = [entry['row'] for entry in entries if entry['pack_size'] is None]

        if len(sizes) > 1:
            conflicts.append({
                'item_code': item_code,
                'pack_sizes': sizes,
                'rows': [entry['row'] for entry in entries]
            })
            continue

        if len(sizes) == 1:
            valid_pack_sizes[item_code] = sizes[0]
            if blank_rows:
                blank_warnings.append({
                    'item_code': item_code,
                    'pack_size': sizes[0],
                    'blank_rows': blank_rows
                })
        else:
            invalid_rows.append({
                'rows': blank_rows,
                'item_code': item_code,
                'reason': 'No pack size provided'
            })

    return valid_pack_sizes, conflicts, blank_warnings, invalid_rows


def _serialize_pack_size(record: PackSize):
    return {
        'id': record.id,
        'item_code': record.item_code,
        'pack_size': record.pack_size,
        'created_at': record.created_at.isoformat() if record.created_at else None,
        'updated_at': record.updated_at.isoformat() if record.updated_at else None
    }


@router.get("/pack-sizes/catalog")
async def list_pack_size_catalog(db: Session = Depends(get_db)):
    records = db.query(PackSize).order_by(PackSize.item_code).all()
    return {'success': True, 'items': [_serialize_pack_size(record) for record in records]}


@router.post("/pack-sizes/process")
async def process_pack_sizes(
    payload: ProcessPackSizesRequest,
    db: Session = Depends(get_db)
):
    valid_pack_sizes, conflicts, blank_warnings, invalid_rows = _parse_pack_size_processor_text(payload.text)

    if blank_warnings and not payload.confirm_blank_duplicates:
        return {
            'success': False,
            'requires_confirmation': True,
            'blank_warnings': blank_warnings,
            'conflicts': conflicts,
            'invalid_rows': invalid_rows,
            'valid_count': len(valid_pack_sizes)
        }

    saved_items = []
    try:
        for item_code, pack_size in valid_pack_sizes.items():
            record = db.query(PackSize).filter(PackSize.item_code == item_code).first()
            if record:
                record.pack_size = pack_size
                record.updated_at = datetime.utcnow()
            else:
                record = PackSize(item_code=item_code, pack_size=pack_size)
                db.add(record)
            saved_items.append(record)

        db.commit()
        for record in saved_items:
            db.refresh(record)

        return {
            'success': True,
            'saved_count': len(saved_items),
            'items': [_serialize_pack_size(record) for record in saved_items],
            'conflicts': conflicts,
            'blank_warnings': blank_warnings,
            'invalid_rows': invalid_rows
        }
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="One or more items already exist")
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(error))


@router.put("/pack-sizes/catalog/{pack_size_id}")
async def update_pack_size_catalog_entry(
    pack_size_id: int,
    payload: UpdatePackSizeRequest,
    db: Session = Depends(get_db)
):
    record = db.query(PackSize).filter(PackSize.id == pack_size_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Pack-size entry not found")

    item_code = _normalize_item_code(payload.item_code)
    if not item_code or payload.pack_size <= 0:
        raise HTTPException(status_code=400, detail="Item number and a positive pack size are required")

    duplicate = db.query(PackSize).filter(
        PackSize.item_code == item_code,
        PackSize.id != pack_size_id
    ).first()
    if duplicate:
        raise HTTPException(status_code=409, detail=f"Item {item_code} already exists")

    record.item_code = item_code
    record.pack_size = payload.pack_size
    record.updated_at = datetime.utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Item {item_code} already exists")
    db.refresh(record)
    return {'success': True, 'item': _serialize_pack_size(record)}


@router.delete("/pack-sizes/catalog/{pack_size_id}")
async def delete_pack_size_catalog_entry(pack_size_id: int, db: Session = Depends(get_db)):
    record = db.query(PackSize).filter(PackSize.id == pack_size_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Pack-size entry not found")

    db.delete(record)
    db.commit()
    return {'success': True, 'deleted_id': pack_size_id}


@router.post("/pack-sizes/parse")
async def parse_pack_sizes_excel(file: UploadFile = File(...)):
    """
    Parse an uploaded Excel sheet of pack sizes.

    If the header row contains a column with "item" in its name and a column
    with "pack" in its name (case-insensitive), those columns are used.
    Otherwise falls back to column 1 = item #, column 2 = pack size.
    """
    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents), header=None, dtype=str)
        pack_sizes = _extract_pack_sizes_from_rows(df.values.tolist())

        if not pack_sizes:
            raise HTTPException(status_code=400, detail="No valid item #/pack size rows found in the uploaded file")

        return {
            'success': True,
            'count': len(pack_sizes),
            'pack_sizes': pack_sizes
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse Excel file: {str(e)}")


@router.post("/pack-sizes/parse-text")
async def parse_pack_sizes_text(payload: PastePackSizesRequest):
    """
    Parse pasted item #/pack size data (e.g. copied straight out of Excel).
    Cells are split on tabs when present, otherwise on commas or 2+ spaces.
    """
    try:
        lines = [line for line in payload.text.splitlines() if line.strip() != '']
        rows = []
        for line in lines:
            cells = line.split('\t') if '\t' in line else re.split(r',|\s{2,}', line.strip())
            rows.append([cell.strip() for cell in cells])

        pack_sizes = _extract_pack_sizes_from_rows(rows)

        if not pack_sizes:
            raise HTTPException(status_code=400, detail="No valid item #/pack size rows found in the pasted text")

        return {
            'success': True,
            'count': len(pack_sizes),
            'pack_sizes': pack_sizes
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse pasted text: {str(e)}")


@router.post("/pallet-numbers/parse")
async def parse_pallet_numbers_excel(file: UploadFile = File(...)):
    """
    Parse an uploaded Excel sheet mapping item # to pallet number (and optional weight).

    If the header row contains a column with "item", "pallet", and/or "weight"
    in its name (case-insensitive), those columns are used. Otherwise falls
    back to column 1 = item #, column 2 = pallet number, column 3 = weight.
    If the same pallet number has conflicting weight values across rows, the
    whole upload is rejected so the file can be fixed and re-submitted.
    """
    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents), header=None, dtype=str)
        pallet_numbers, pallet_weights, weight_conflicts = _extract_pallet_numbers_from_rows(df.values.tolist())

        if not pallet_numbers:
            raise HTTPException(status_code=400, detail="No valid item #/pallet number rows found in the uploaded file")

        if weight_conflicts:
            conflict_summary = '; '.join(
                f"pallet {c['pallet_number']} has conflicting weights {c['weights']}" for c in weight_conflicts
            )
            raise HTTPException(
                status_code=400,
                detail=f"Weight discrepancy found, nothing was applied: {conflict_summary}. Fix the file and try again."
            )

        return {
            'success': True,
            'count': len(pallet_numbers),
            'pallet_numbers': pallet_numbers,
            'pallet_weights': pallet_weights
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse Excel file: {str(e)}")


@router.post("/pallet-numbers/parse-text")
async def parse_pallet_numbers_text(payload: PastePalletNumbersRequest):
    """
    Parse pasted item #/pallet number/weight data (e.g. copied straight out of Excel).
    Cells are split on tabs when present, otherwise on commas or 2+ spaces.
    If the same pallet number has conflicting weight values across rows, the
    whole paste is rejected so the data can be fixed and re-submitted.
    """
    try:
        lines = [line for line in payload.text.splitlines() if line.strip() != '']
        rows = []
        for line in lines:
            cells = line.split('\t') if '\t' in line else re.split(r',|\s{2,}', line.strip())
            rows.append([cell.strip() for cell in cells])

        pallet_numbers, pallet_weights, weight_conflicts = _extract_pallet_numbers_from_rows(rows)

        if not pallet_numbers:
            raise HTTPException(status_code=400, detail="No valid item #/pallet number rows found in the pasted text")

        if weight_conflicts:
            conflict_summary = '; '.join(
                f"pallet {c['pallet_number']} has conflicting weights {c['weights']}" for c in weight_conflicts
            )
            raise HTTPException(
                status_code=400,
                detail=f"Weight discrepancy found, nothing was applied: {conflict_summary}. Fix the pasted data and try again."
            )

        return {
            'success': True,
            'count': len(pallet_numbers),
            'pallet_numbers': pallet_numbers,
            'pallet_weights': pallet_weights
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse pasted text: {str(e)}")


def _get_primary_customer_order_link(shipment: dict):
    customer_order_id = shipment.get("customer_order_id")
    customer_order_code = shipment.get("customer_order_code")

    if customer_order_id is not None or customer_order_code:
        return customer_order_id, customer_order_code

    for linked_order in shipment.get("orders", []) or []:
        linked_id = linked_order.get("customer_order_id")
        linked_code = linked_order.get("customer_order_code")
        if linked_id is not None or linked_code:
            return linked_id, linked_code

    return None, None


def _get_customer_order_details(shipment: dict):
    customer_order_id, customer_order_code = _get_primary_customer_order_link(shipment)
    customer_order = None

    if customer_order_id is not None:
        try:
            customer_order = mrpeasy_client.get_customer_order(customer_order_id)
        except Exception as e:
            print(f"Error fetching customer order {customer_order_id}: {e}")
    elif customer_order_code:
        try:
            customer_orders = mrpeasy_client.get_customer_orders()
            customer_order = next((order for order in customer_orders if order.get("code") == customer_order_code), None)
        except Exception as e:
            print(f"Error fetching customer order {customer_order_code}: {e}")

    return {
        "customer_order": customer_order,
        "customer_order_id": customer_order_id,
        "customer_order_code": customer_order_code,
    }


def _format_shipping_address(address_obj) -> Optional[str]:
    """
    Format a raw MRPeasy address object/string into a multi-line address:
    Company / Street 1 / Street 2 / City State Zip / Country
    """
    if not address_obj:
        return None

    if isinstance(address_obj, str):
        try:
            address_obj = json.loads(address_obj)
        except (ValueError, TypeError):
            stripped = address_obj.strip()
            return stripped or None

    if not isinstance(address_obj, dict):
        return None

    company = address_obj.get('company') or address_obj.get('name')
    street1 = address_obj.get('street_line_1') or address_obj.get('line1') or address_obj.get('address_1')
    street2 = address_obj.get('street_line_2') or address_obj.get('line2') or address_obj.get('address_2')
    city = address_obj.get('city')
    state = address_obj.get('state')
    postal_code = address_obj.get('postal_code') or address_obj.get('zip')
    country = address_obj.get('country')

    lines = []
    if company:
        lines.append(str(company).strip())
    if street1:
        lines.append(str(street1).strip())
    if street2:
        lines.append(str(street2).strip())

    city_state_zip = ' '.join(str(p).strip() for p in [city, state, postal_code] if p)
    if city_state_zip:
        lines.append(city_state_zip)
    if country:
        lines.append(str(country).strip())

    return '\n'.join(lines) if lines else None


def extract_job_number(order: dict) -> str:
    candidate = order.get("custom_814")
    if candidate is None:
        candidate = order.get("custom_531")

    if candidate is None:
        return "N/A"

    if isinstance(candidate, (int, float)):
        value = int(candidate)
        if value < 1000000000:
            return str(value)
        return "N/A"

    if isinstance(candidate, str):
        trimmed = candidate.strip()
        if not trimmed:
            return "N/A"
        lowered = trimmed.lower()
        if lowered in {"partial", "complete", "open"}:
            return "N/A"
        if trimmed.isdigit() and int(trimmed) >= 1000000000:
            return "N/A"
        return trimmed

    return "N/A"

def calculate_boxes(quantity: int, pack_size: int):
    """Calculate number of boxes and remaining items"""
    if pack_size <= 0:
        pack_size = 1
    
    full_boxes = quantity // pack_size
    remaining = quantity % pack_size
    
    boxes = []
    
    # Add full boxes
    for i in range(full_boxes):
        boxes.append({
            'box_number': i + 1,
            'quantity': pack_size
        })
    
    # Add partial box if there's a remainder
    if remaining > 0:
        boxes.append({
            'box_number': full_boxes + 1,
            'quantity': remaining
        })
    
    return boxes


def _format_box_info(boxes: List[ShipmentBox]) -> str:
    boxes_by_quantity = {}
    for box in boxes:
        boxes_by_quantity[box.quantity_in_box] = boxes_by_quantity.get(box.quantity_in_box, 0) + 1

    return '\r\n'.join(
        f"{quantity} x {count} Box"
        for quantity, count in sorted(boxes_by_quantity.items(), reverse=True)
    )


def _prepare_shipment_box_info_sync(shipment_code: str, db: Session) -> Dict:
    boxes = (
        db.query(ShipmentBox)
        .filter(ShipmentBox.shipment_code == shipment_code)
        .order_by(ShipmentBox.id)
        .all()
    )
    if not boxes:
        raise HTTPException(status_code=404, detail=f"No finalized boxes found for {shipment_code}")

    customer_order_code = boxes[0].customer_order_code
    if not customer_order_code:
        raise HTTPException(status_code=400, detail="Finalized shipment has no linked customer order")
    if any(box.customer_order_code != customer_order_code for box in boxes):
        raise HTTPException(status_code=409, detail="Shipment box rows reference multiple customer orders")

    matching_orders = mrpeasy_client.get_customer_orders({'code': customer_order_code})
    customer_order = next(
        (order for order in matching_orders if order.get('code') == customer_order_code),
        None
    )
    if not customer_order:
        raise HTTPException(status_code=404, detail=f"Customer order {customer_order_code} not found")

    customer_order_id = customer_order.get('customer_order_id') or customer_order.get('cust_ord_id')
    if customer_order_id is None:
        raise HTTPException(status_code=400, detail=f"Customer order {customer_order_code} has no ID")

    order_lines = {}
    for product in customer_order.get('products', []) or []:
        key = (_normalize_item_code(product.get('item_code')), str(product.get('ord') or ''))
        order_lines.setdefault(key, []).append(product)

    grouped_boxes = {}
    for box in boxes:
        key = (_normalize_item_code(box.item_code), str(box.order_line or ''))
        grouped_boxes.setdefault(key, []).append(box)

    updates = []
    allowed_lot_ids = set()
    for key, line_boxes in grouped_boxes.items():
        matches = order_lines.get(key, [])
        if len(matches) != 1:
            item_code, order_line = key
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Expected exactly one order line for item {item_code}, line {order_line}; "
                    f"found {len(matches)}"
                )
            )

        order_line = matches[0]
        line_id = order_line.get('line_id')
        quantity = order_line.get('quantity')
        if line_id is None or quantity is None:
            raise HTTPException(
                status_code=400,
                detail=f"Order line {order_line.get('ord')} is missing line_id or quantity"
            )

        shipment_lot_codes = set()
        for box in line_boxes:
            try:
                shipment_lot_codes.update(json.loads(box.lot_codes or '[]'))
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=409,
                    detail=f"Shipment {shipment_code} has invalid lot data for order line {box.order_line}"
                )
        shipment_lot_codes.discard(None)
        shipment_lot_codes.discard('')
        if not shipment_lot_codes:
            raise HTTPException(
                status_code=409,
                detail=f"Shipment {shipment_code} has no lots for order line {order_line.get('ord')}"
            )

        source_lot_ids = {
            source.get('lot_code'): source.get('lot_id')
            for source in order_line.get('source', []) or []
        }
        missing_lots = sorted(shipment_lot_codes - set(source_lot_ids))
        if missing_lots:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Shipment lots are not booked on order line {order_line.get('ord')}: "
                    f"{', '.join(missing_lots)}"
                )
            )
        line_lot_ids = {source_lot_ids[lot_code] for lot_code in shipment_lot_codes}
        if None in line_lot_ids:
            raise HTTPException(
                status_code=409,
                detail=f"MRPeasy lot ID is missing on order line {order_line.get('ord')}"
            )
        allowed_lot_ids.update(line_lot_ids)

        updates.append({
            'line_id': line_id,
            'quantity': quantity,
            'description': _format_box_info(line_boxes)
        })

    return {
        'shipment_code': shipment_code,
        'customer_order': customer_order,
        'customer_order_id': customer_order_id,
        'customer_order_code': customer_order_code,
        'updates': updates,
        'allowed_lot_ids': sorted(allowed_lot_ids)
    }


@router.get("/shipments/{shipment_code}/box-info-preview")
def preview_shipment_box_info_for_order(
    shipment_code: str,
    db: Session = Depends(get_db)
):
    """Preview finalized box data matched to MRPeasy customer-order lines without writing."""
    prepared = _prepare_shipment_box_info_sync(shipment_code, db)
    return {
        'success': True,
        'read_only': True,
        'shipment_code': prepared['shipment_code'],
        'customer_order_id': prepared['customer_order_id'],
        'customer_order_code': prepared['customer_order_code'],
        'allowed_lot_ids': prepared['allowed_lot_ids'],
        'line_mappings': [
            {
                'line_id': update['line_id'],
                'box_info_per_item': update['description']
            }
            for update in prepared['updates']
        ]
    }


@router.post("/shipments/{shipment_code}/box-info-sync")
def sync_shipment_box_info_to_order(
    shipment_code: str,
    current_user: User = Depends(require_permission("sync")),
    db: Session = Depends(get_db)
):
    """Write finalized box data while preventing MRPeasy from booking unrelated stock."""
    prepared = _prepare_shipment_box_info_sync(shipment_code, db)
    before = prepared['customer_order']
    before_lines = {
        product.get('line_id'): product
        for product in before.get('products', []) or []
    }
    changed_updates = [
        update for update in prepared['updates']
        if before_lines[update['line_id']].get('description') != update['description']
    ]
    if not changed_updates:
        return {
            'success': True,
            'updated': False,
            'shipment_code': shipment_code,
            'customer_order_code': prepared['customer_order_code'],
            'changed_line_count': 0
        }

    before_status = before.get('status')
    before_sources = {
        line_id: product.get('source', []) or []
        for line_id, product in before_lines.items()
    }
    mrpeasy_client.update_customer_order(
        prepared['customer_order_id'],
        {
            'lot_id': prepared['allowed_lot_ids'],
            'products': changed_updates
        }
    )

    after = mrpeasy_client.get_customer_order(prepared['customer_order_id'])
    after_lines = {
        product.get('line_id'): product
        for product in after.get('products', []) or []
    }
    after_sources = {
        line_id: product.get('source', []) or []
        for line_id, product in after_lines.items()
    }
    if after.get('status') != before_status or after_sources != before_sources:
        raise HTTPException(
            status_code=409,
            detail=(
                "MRPeasy changed the order status or stock bookings unexpectedly. "
                "Review the order before attempting another synchronization."
            )
        )

    failed_line_ids = [
        update['line_id'] for update in changed_updates
        if after_lines.get(update['line_id'], {}).get('description') != update['description']
    ]
    if failed_line_ids:
        raise HTTPException(
            status_code=502,
            detail=f"MRPeasy did not save box info for line IDs: {failed_line_ids}"
        )

    return {
        'success': True,
        'updated': True,
        'shipment_code': shipment_code,
        'customer_order_code': prepared['customer_order_code'],
        'changed_line_count': len(changed_updates)
    }

@router.get("/shipments/ready")
async def get_ready_shipments():
    """Get all shipments that are ready for labeling"""
    try:
        shipments = mrpeasy_client.get_shipments()
        
        # Filter for ready shipments
        ready_shipments = [
            {
                'code': s.get('code'),
                'customer_order_code': _get_primary_customer_order_link(s)[1],
                'status_txt': s.get('status_txt'),
                'status_id': s.get('status_id'),
                'products_count': len(s.get('products', []))
            }
            for s in shipments 
            if 'ready' in s.get('status_txt', '').lower()
        ]
        
        return {
            'success': True,
            'count': len(ready_shipments),
            'shipments': ready_shipments
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/shipments/archived")
async def get_archived_shipments():
    """Shipments no longer in 'ready' status (e.g. already shipped in MRPeasy).

    These fall out of /shipments/ready once MRPeasy marks them shipped, but
    their product/lot data is still available from MRPeasy so labels and
    packing slips can still be generated/reprinted after the fact.
    """
    try:
        shipments = mrpeasy_client.get_shipments()

        archived_shipments = [
            {
                'code': s.get('code'),
                'customer_order_code': _get_primary_customer_order_link(s)[1],
                'status_txt': s.get('status_txt'),
                'status_id': s.get('status_id'),
                'products_count': len(s.get('products', []))
            }
            for s in shipments
            if 'ready' not in s.get('status_txt', '').lower()
        ]

        return {
            'success': True,
            'count': len(archived_shipments),
            'shipments': archived_shipments
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/shipments/{shipment_code}")
async def get_shipment_details(shipment_code: str):
    """Get detailed information about a specific shipment"""
    try:
        shipments = mrpeasy_client.get_shipments()
        
        # Find the specific shipment
        shipment = next((s for s in shipments if s.get('code') == shipment_code), None)
        
        if not shipment:
            raise HTTPException(status_code=404, detail=f"Shipment {shipment_code} not found")
        
        # Get customer order details to fetch customer name, reference, and order line info
        order_details = _get_customer_order_details(shipment)
        customer_order_id = order_details['customer_order_id']
        customer_order_code = order_details['customer_order_code']
        customer_name = None
        reference = None
        customer_order = order_details['customer_order']

        if customer_order:
            customer_name = customer_order.get('customer_name')
            reference = customer_order.get('reference')
        
        # Enrich shipment products with order line info
        # Strategy: For each shipment product, find which order line it belongs to
        # by checking which order line's source list contains that lot code
        # Also track cumulative quantity per order line to handle shared lots correctly
        
        if customer_order:
            shipment_products = shipment.get('products', [])
            
            # Build a map: for each (item_code, ord), track which lots are in source, remaining qty, and total qty
            # Structure: (item_code, ord) -> {'lots': set of lot_codes, 'remaining': qty, 'total': qty, 'shipped': qty}
            order_line_needs = {}
            for order_product in customer_order.get('products', []):
                ord_num = order_product.get('ord')
                item_code = order_product.get('item_code')
                qty = order_product.get('quantity', 0)
                shipped = order_product.get('shipped', 0)
                
                lots = set()
                for source in order_product.get('source', []):
                    lot_code = source.get('lot_code')
                    if lot_code:
                        lots.add(lot_code)
                
                key = (item_code, ord_num)
                order_line_needs[key] = {
                    'lots': lots,
                    'remaining': qty,
                    'total': qty,
                    'shipped': shipped
                }
            
            # Now assign each shipment product to the appropriate order line
            for product in shipment_products:
                lot_code = product.get('lot_code')
                qty_booked = product.get('quantity_booked') or product.get('quantity') or 0
                item_code = product.get('item_code')
                
                # Find which order line this product belongs to
                # Match by (item_code, ord) where lot_code is in that line's sources
                assigned = False
                for (need_item, need_ord), line_info in order_line_needs.items():
                    if need_item == item_code and lot_code in line_info['lots'] and line_info['remaining'] > 0:
                        product['order_line'] = need_ord
                        product['qty_remaining'] = line_info['total'] - line_info['shipped']
                        line_info['remaining'] -= qty_booked
                        assigned = True
                        break
                
                # Fallback: if not assigned, use first order line for this item
                if not assigned:
                    for (need_item, need_ord) in sorted(order_line_needs.keys()):
                        if need_item == item_code:
                            product['order_line'] = need_ord
                            product['qty_remaining'] = order_line_needs[(need_item, need_ord)]['total'] - order_line_needs[(need_item, need_ord)]['shipped']
                            break
                            break
        
        # Add customer info to shipment data
        shipment['customer_order_id'] = customer_order_id
        shipment['customer_order_code'] = customer_order_code
        shipment['customer_name'] = customer_name
        shipment['reference'] = reference
        
        return {
            'success': True,
            'shipment': shipment
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate/{shipment_code}")
async def generate_labels(
    shipment_code: str,
    label_mode: Literal['individual', 'grouped'] = 'individual',
    product_configs: Dict = Body(default={})
):
    """
    Generate labels for a shipment
    
    Args:
        shipment_code: The shipment code to generate labels for
        label_mode: 'individual' for one label per box, 'grouped' for one label per unique quantity
        product_configs: Dictionary mapping product keys to config objects
                        e.g., {"item_code-0": {"item_code": "51753", "order_line": "1", "pack_size": 40}}
    """
    try:
        shipments = mrpeasy_client.get_shipments()
        
        # Find the specific shipment
        shipment = next((s for s in shipments if s.get('code') == shipment_code), None)
        
        if not shipment:
            raise HTTPException(status_code=404, detail=f"Shipment {shipment_code} not found")
        
        # Get customer order details to fetch customer name and reference
        order_details = _get_customer_order_details(shipment)
        customer_order = order_details['customer_order']
        customer_order_code = order_details['customer_order_code']
        customer_name = None
        reference = None
        job_number = "N/A"

        if customer_order:
            customer_name = customer_order.get('customer_name')
            reference = customer_order.get('reference')
            job_number = extract_job_number(customer_order)
        
        # Default product configs
        if not product_configs:
            product_configs = {}
        
        # Build a map of shipment products by index for quick lookup
        shipment_products = shipment.get('products', [])
        
        # Group product configs by (item_code, order_line) to combine items from same order line
        combined_groups = {}
        for product_key, config in product_configs.items():
            item_code = config.get('item_code')
            order_line = config.get('order_line', '1')
            pack_size = config.get('pack_size', 1)
            
            # Extract product index from key (e.g., "item_code-0" → 0)
            try:
                prod_index = int(product_key.split('-')[-1])
                if prod_index >= len(shipment_products):
                    continue
                product = shipment_products[prod_index]
            except (ValueError, IndexError):
                continue
            
            # Group by (item_code, order_line) - these should be combined into one label
            group_key = (item_code, order_line)
            if group_key not in combined_groups:
                combined_groups[group_key] = {
                    'item_code': item_code,
                    'item_title': product.get('item_title'),
                    'order_line': order_line,
                    'pack_size': pack_size,
                    'lot_codes': [],
                    'total_quantity': 0,
                    'products': []
                }
            
            combined_groups[group_key]['lot_codes'].append(product.get('lot_code'))
            combined_groups[group_key]['total_quantity'] += product.get('quantity_booked') or product.get('quantity') or 0
            combined_groups[group_key]['products'].append(product)
        
        all_labels = []
        
        for (item_code, order_line), item_data in combined_groups.items():
            item_title = item_data['item_title']
            quantity = item_data['total_quantity']
            lot_codes = item_data['lot_codes']
            pack_size = item_data['pack_size']
            
            # Use first lot code or combine them
            lot_code = lot_codes[0] if len(lot_codes) == 1 else ', '.join(lot_codes)
            
            # Calculate boxes
            boxes = calculate_boxes(quantity, pack_size)
            
            if label_mode == 'individual':
                # Individual mode: one label per box
                for box in boxes:
                    label = {
                        'shipment_code': shipment.get('code'),
                        'customer_order': customer_order_code,
                        'customer_name': customer_name,
                        'reference': reference,
                        'job_number': job_number,
                        'item_code': item_code,
                        'item_title': item_title,
                        'lot_code': lot_code,
                        'box_number': box['box_number'],
                        'total_boxes': len(boxes),
                        'quantity_in_box': box['quantity'],
                        'total_quantity': quantity,
                        'label_type': 'individual',
                        'pack_size': pack_size
                    }
                    all_labels.append(label)
            
            else:
                # Grouped mode: one label per unique quantity
                qty_groups = {}
                for box in boxes:
                    qty = box['quantity']
                    if qty not in qty_groups:
                        qty_groups[qty] = []
                    qty_groups[qty].append(box['box_number'])
                
                for qty, box_numbers in qty_groups.items():
                    label = {
                        'shipment_code': shipment.get('code'),
                        'customer_order': customer_order_code,
                        'customer_name': customer_name,
                        'reference': reference,
                        'job_number': job_number,
                        'item_code': item_code,
                        'item_title': item_title,
                        'lot_code': lot_code,
                        'box_count': len(box_numbers),
                        'box_numbers': box_numbers,
                        'total_boxes': len(boxes),
                        'quantity_in_box': qty,
                        'total_quantity': quantity,
                        'label_type': 'grouped',
                        'pack_size': pack_size
                    }
                    all_labels.append(label)
        
        return {
            'success': True,
            'shipment_code': shipment_code,
            'label_mode': label_mode,
            'total_labels': len(all_labels),
            'labels': all_labels
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/finalize/{shipment_code}")
async def delete_finalized_shipment(shipment_code: str, db: Session = Depends(get_db)):
    """
    Delete the finalized box configuration for a shipment so it can be regenerated
    """
    try:
        deleted_count = db.query(ShipmentBox).filter(
            ShipmentBox.shipment_code == shipment_code
        ).delete()
        db.commit()

        if deleted_count == 0:
            raise HTTPException(status_code=404, detail=f"No finalized boxes found for {shipment_code}")

        return {
            'success': True,
            'shipment_code': shipment_code,
            'deleted_boxes': deleted_count
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/finalize/{shipment_code}")
def finalize_shipment_configuration(
    shipment_code: str,
    request: FinalizeShipmentRequest,
    db: Session = Depends(get_db)
):
    """
    Finalize and lock shipment box configuration - saves to database
    
    Args:
        shipment_code: The shipment code
        request: Contains pallet_number and product_configs
                Dict format: {"item_code-0": {"item_code": "51753", "order_line": "1", "pack_size": 40}}
    """
    try:
        shipments = mrpeasy_client.get_shipments()
        shipment = next((s for s in shipments if s.get('code') == shipment_code), None)
        
        if not shipment:
            raise HTTPException(status_code=404, detail=f"Shipment {shipment_code} not found")
        
        order_details = _get_customer_order_details(shipment)
        customer_order = order_details['customer_order']
        customer_order_id = order_details['customer_order_id']
        customer_order_code = order_details['customer_order_code']
        shipment_products = shipment.get('products', [])
        pallet_number = request.pallet_number
        product_configs = request.product_configs

        if not product_configs:
            raise HTTPException(status_code=400, detail="No product configurations were provided")
        
        # Fetch PO number, customer name, job #, and shipping address from customer order
        po_number = None
        customer_name = None
        shipping_address = None
        job_number = None
        if customer_order:
            try:
                po_number = customer_order.get('reference') or customer_order.get('code')
                customer_name = customer_order.get('customer_name')
                job_number = extract_job_number(customer_order)

                address_obj = (
                    customer_order.get('delivery_address')
                    or customer_order.get('shipping_address')
                    or customer_order.get('address')
                )
                shipping_address = _format_shipping_address(address_obj)
            except Exception as e:
                print(f"Warning: Could not fetch customer order info: {e}")

        if not shipping_address:
            address_obj = shipment.get('delivery_address') or shipment.get('shipping_address')
            shipping_address = _format_shipping_address(address_obj)

        # Delivery date comes from the shipment itself (falls back to the customer order)
        delivery_date = shipment.get('delivery_date') or (customer_order.get('delivery_date') if customer_order else None)
        
        # Pool all lots for the same item_code + order_line before splitting into boxes,
        # so the box breakdown reflects the pack size entered for that item, not per-lot MRP quantities.
        groups = {}
        for product_key, config in product_configs.items():
            item_code = config.get('item_code')
            order_line = config.get('order_line', '1')
            pack_size = config.get('pack_size', 1)

            try:
                prod_index = int(product_key.split('-')[-1])
                if prod_index >= len(shipment_products):
                    continue
                product = shipment_products[prod_index]
            except (ValueError, IndexError):
                continue

            lot_code = product.get('lot_code', '')
            quantity_booked = product.get('quantity_booked') or product.get('quantity') or 0
            item_title = product.get('item_title', '')

            group_key = (item_code, order_line)
            if group_key not in groups:
                groups[group_key] = {
                    'item_title': item_title,
                    'pack_size': pack_size,
                    'total_quantity': 0,
                    'lot_codes': []
                }
            groups[group_key]['total_quantity'] += quantity_booked
            groups[group_key]['pack_size'] = pack_size
            if lot_code:
                groups[group_key]['lot_codes'].append(lot_code)

        if not groups:
            raise HTTPException(
                status_code=400,
                detail="None of the product configurations matched this shipment"
            )

        if not any(group['total_quantity'] > 0 for group in groups.values()):
            raise HTTPException(status_code=400, detail="Shipment products have no quantity to pack")

        # Replace existing rows in the same transaction as the new rows. If any
        # replacement fails, rollback preserves the previous packing list.
        db.query(ShipmentBox).filter(
            ShipmentBox.shipment_code == shipment_code
        ).delete(synchronize_session=False)

        # Create new shipment box records from the pooled per-item totals
        saved_boxes = []
        for (item_code, order_line), group in groups.items():
            boxes = calculate_boxes(group['total_quantity'], group['pack_size'])

            for box in boxes:
                shipment_box = ShipmentBox(
                    shipment_code=shipment_code,
                    customer_order_code=customer_order_code,
                    po_number=po_number,
                    customer_name=customer_name,
                    shipping_address=shipping_address,
                    job_number=job_number,
                    delivery_date=str(delivery_date) if delivery_date else None,
                    item_code=item_code,
                    item_title=group['item_title'],
                    order_line=order_line,
                    pack_size=group['pack_size'],
                    box_number=box['box_number'],
                    quantity_in_box=box['quantity'],
                    total_quantity=group['total_quantity'],
                    lot_codes=json.dumps(group['lot_codes']),
                    pallet_number=pallet_number,
                    generated_from='finalized'
                )
                db.add(shipment_box)
                saved_boxes.append({
                    'item_code': item_code,
                    'order_line': order_line,
                    'box_number': box['box_number'],
                    'quantity': box['quantity']
                })

        if not saved_boxes:
            raise HTTPException(status_code=400, detail="No packing-list boxes were generated")
        
        db.commit()
        
        return {
            'success': True,
            'shipment_code': shipment_code,
            'pallet_number': pallet_number,
            'total_boxes_saved': len(saved_boxes),
            'boxes': saved_boxes
        }
    
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/finalize-batch")
def finalize_shipment_batch(
    request: BulkFinalizeRequest,
    db: Session = Depends(get_db)
):
    """Finalize every submitted shipment server-side, even if the browser disconnects."""
    if not request.shipments:
        raise HTTPException(status_code=400, detail="No shipments were provided")

    results = []
    failures = []
    total_boxes_saved = 0

    for shipment_request in request.shipments:
        try:
            result = finalize_shipment_configuration(
                shipment_request.shipment_code,
                FinalizeShipmentRequest(
                    pallet_number=shipment_request.pallet_number,
                    product_configs=shipment_request.product_configs
                ),
                db
            )
            results.append(result)
            total_boxes_saved += result.get('total_boxes_saved', 0)
        except HTTPException as error:
            failures.append({
                'shipment_code': shipment_request.shipment_code,
                'status_code': error.status_code,
                'detail': error.detail
            })
        except Exception as error:
            db.rollback()
            failures.append({
                'shipment_code': shipment_request.shipment_code,
                'status_code': 500,
                'detail': str(error)
            })

    return {
        'success': not failures,
        'success_count': len(results),
        'failure_count': len(failures),
        'total_boxes_saved': total_boxes_saved,
        'results': results,
        'failures': failures
    }


@router.get("/shipments/{shipment_code}/finalized-labels")
def get_finalized_labels(shipment_code: str, db: Session = Depends(get_db)):
    """Return finalized box labels without modifying shipment data."""
    boxes = (
        db.query(ShipmentBox)
        .filter(ShipmentBox.shipment_code == shipment_code)
        .order_by(ShipmentBox.item_code, ShipmentBox.order_line, ShipmentBox.box_number, ShipmentBox.id)
        .all()
    )
    labels = _serialize_finalized_labels(boxes)
    return {
        'success': True,
        'read_only': True,
        'finalized': bool(labels),
        'shipment_code': shipment_code,
        'total_labels': len(labels),
        'labels': labels
    }


@router.put("/shipments/finalized/{shipment_code}/pallet-numbers")
def update_pallet_numbers(
    shipment_code: str,
    payload: UpdatePalletNumbersRequest,
    db: Session = Depends(get_db)
):
    """
    Assign/edit pallet numbers per item on an already-finalized shipment.

    This is the only field allowed to change after finalize (pack sizes,
    box breakdowns, and other finalized data stay locked). Works the same
    whether the shipment is still 'ready' in MRPeasy or already archived,
    since pallet data is purely local. Keyed by "item_code:order_line";
    a blank value clears the pallet number for that item.
    """
    boxes = db.query(ShipmentBox).filter(ShipmentBox.shipment_code == shipment_code).all()
    if not boxes:
        raise HTTPException(status_code=404, detail=f"No finalized boxes found for {shipment_code}")

    updated_count = 0
    for key, pallet_number in payload.pallet_numbers.items():
        item_code, _, order_line = key.partition(':')
        order_line = order_line or '1'
        normalized_pallet = (pallet_number or '').strip() or None

        for box in boxes:
            if box.item_code == item_code and str(box.order_line or '1') == order_line:
                box.pallet_number = normalized_pallet
                updated_count += 1

    db.commit()
    return {'success': True, 'shipment_code': shipment_code, 'updated_boxes': updated_count}


@router.get("/shipments/finalized/{shipment_code}/pallet-weights")
def get_pallet_weights(shipment_code: str, db: Session = Depends(get_db)):
    records = db.query(PalletWeight).filter(PalletWeight.shipment_code == shipment_code).all()
    return {
        'success': True,
        'shipment_code': shipment_code,
        'weights': {record.pallet_number: record.weight for record in records}
    }


@router.put("/shipments/finalized/{shipment_code}/pallet-weights")
def update_pallet_weights(
    shipment_code: str,
    payload: UpdatePalletWeightsRequest,
    db: Session = Depends(get_db)
):
    """Save the weight entered for each pallet number within a shipment."""
    updated = []
    for pallet_number, weight in payload.weights.items():
        pallet_number = (pallet_number or '').strip()
        if not pallet_number:
            continue

        record = db.query(PalletWeight).filter(
            PalletWeight.shipment_code == shipment_code,
            PalletWeight.pallet_number == pallet_number
        ).first()

        if record:
            record.weight = weight
            record.updated_at = datetime.utcnow()
        else:
            record = PalletWeight(shipment_code=shipment_code, pallet_number=pallet_number, weight=weight)
            db.add(record)
        updated.append(pallet_number)

    db.commit()
    return {'success': True, 'shipment_code': shipment_code, 'updated_pallets': updated}


@router.get("/shipments/finalized/{shipment_code}/packing-data")
async def get_packing_slip_data(shipment_code: str, db: Session = Depends(get_db)):
    """
    Get packing slip data from database for a finalized shipment
    
    Groups by order_line and combines duplicate items
    Shows: Item, Description, Total Qty Shipped, Box breakdown (e.g., 3 box of 30 + 2 box of 5)
    """
    try:
        # Get all box records for this shipment, preserving original entry order (not alphabetical)
        boxes = db.query(ShipmentBox).filter(
            ShipmentBox.shipment_code == shipment_code
        ).order_by(ShipmentBox.id).all()
        
        if not boxes:
            raise HTTPException(status_code=404, detail=f"No finalized boxes found for {shipment_code}")
        
        # Group by item_code + order_line to combine duplicates
        grouped_items = {}

        customer_order_code = boxes[0].customer_order_code if boxes else None
        order_lines_map = {}

        # Fetch customer order so each line can include ordered and remaining quantities.
        if customer_order_code:
            try:
                customer_orders = mrpeasy_client.get_customer_orders()
                customer_order = next(
                    (order for order in customer_orders if order.get('code') == customer_order_code),
                    None
                )

                if customer_order:
                    for order_product in customer_order.get('products', []):
                        item_code = order_product.get('item_code')
                        order_line = str(order_product.get('ord') or '1')
                        if not item_code:
                            continue

                        key = (item_code, order_line)
                        qty_ordered = int(order_product.get('quantity') or 0)
                        qty_shipped_total = int(order_product.get('shipped') or 0)
                        qty_remaining = max(qty_ordered - qty_shipped_total, 0)

                        order_lines_map[key] = {
                            'qty_ordered': qty_ordered,
                            'qty_shipped_total': qty_shipped_total,
                            'qty_remaining': qty_remaining
                        }
            except Exception as e:
                print(f"Warning: failed to enrich packing slip with order quantities for {customer_order_code}: {e}")
        
        for box in boxes:
            group_key = f"{box.item_code}:{box.order_line}"
            
            if group_key not in grouped_items:
                grouped_items[group_key] = {
                    'shipment_code': shipment_code,
                    'item_code': box.item_code,
                    'item_title': box.item_title,
                    'order_line': box.order_line,
                    'po_number': box.po_number,
                    'finalized_at': box.finalized_at.strftime('%Y-%m-%d') if box.finalized_at else None,
                    'boxes_by_quantity': {},  # {qty: count}
                    'all_boxes': [],
                    'total_qty_shipped': 0,
                    'pallet_number': box.pallet_number,
                    'lot_codes': []
                }
            
            # Track boxes by quantity (e.g., "30": 3 boxes)
            qty = box.quantity_in_box
            if qty not in grouped_items[group_key]['boxes_by_quantity']:
                grouped_items[group_key]['boxes_by_quantity'][qty] = 0
            grouped_items[group_key]['boxes_by_quantity'][qty] += 1
            
            # Add to total
            grouped_items[group_key]['total_qty_shipped'] += qty
            
            # Track lot codes
            if box.lot_codes:
                lot_list = json.loads(box.lot_codes) if isinstance(box.lot_codes, str) else box.lot_codes
                grouped_items[group_key]['lot_codes'].extend(lot_list)
            
            # Store all box details
            grouped_items[group_key]['all_boxes'].append({
                'box_number': box.box_number,
                'quantity_in_box': box.quantity_in_box,
                'pack_size': box.pack_size
            })
        
        # Format the box breakdown (e.g., "3 box of 30, 2 box of 5")
        packing_slip_items = []
        for group_key, item_data in grouped_items.items():
            # Create box breakdown string
            box_breakdown_parts = []
            for qty in sorted(item_data['boxes_by_quantity'].keys(), reverse=True):
                count = item_data['boxes_by_quantity'][qty]
                box_breakdown_parts.append(f"{count} box of {qty}")
            box_breakdown = ', '.join(box_breakdown_parts)
            
            # Remove duplicates from lot_codes
            unique_lot_codes = list(set(item_data['lot_codes']))
            
            lookup_key = (item_data['item_code'], str(item_data['order_line'] or '1'))
            order_line_data = order_lines_map.get(lookup_key, {})
            qty_ordered = int(order_line_data.get('qty_ordered', item_data['total_qty_shipped']) or 0)
            qty_shipped_current = int(item_data['total_qty_shipped'] or 0)

            # Prefer API-provided order-line totals when available.
            api_total_shipped = order_line_data.get('qty_shipped_total')
            api_remaining = order_line_data.get('qty_remaining')

            if api_total_shipped is not None or api_remaining is not None:
                qty_shipped_total = int(api_total_shipped or 0)
                qty_remaining = int(api_remaining or 0)
            else:
                qty_shipped_total = qty_shipped_current
                qty_remaining = max(qty_ordered - qty_shipped_total, 0)

            qty_shipped_before = max(qty_shipped_total - qty_shipped_current, 0)

            packing_slip_items.append({
                'shipment_code': shipment_code,
                'item_code': item_data['item_code'],
                'item_title': item_data['item_title'],
                'order_line': item_data['order_line'],
                'po_number': item_data['po_number'],
                'finalized_at': item_data['finalized_at'],
                'qty_ordered': qty_ordered,
                'qty_shipped': qty_shipped_current,
                'qty_shipped_before': qty_shipped_before,
                'qty_shipped_total': qty_shipped_total,
                'qty_remaining': qty_remaining,
                'box_breakdown': box_breakdown,  # e.g., "3 box of 30, 2 box of 5"
                'pallet_number': item_data['pallet_number'],
                'lot_codes': unique_lot_codes,
                'all_boxes': item_data['all_boxes']
            })

        # Nut variants (e.g. "15353-NUTS", "15353-NUT", "15353 - nut") inherit their
        # matching bolt/base item's pallet number when no pallet # was explicitly set for them.
        base_item_pallets = {}
        for item_data in packing_slip_items:
            normalized_code = _normalize_item_code(item_data['item_code'])
            if not normalized_code.endswith('-NUT') and item_data.get('pallet_number'):
                base_item_pallets[normalized_code] = item_data['pallet_number']

        for item_data in packing_slip_items:
            if item_data.get('pallet_number'):
                continue
            normalized_code = _normalize_item_code(item_data['item_code'])
            if normalized_code.endswith('-NUT'):
                base_code = normalized_code[:-len('-NUT')]
                if base_code in base_item_pallets:
                    item_data['pallet_number'] = base_item_pallets[base_code]

        # Group items by pallet number for the pallet summary table (optional field, skip unassigned items)
        pallet_groups = {}
        for item_data in packing_slip_items:
            pallet_number = item_data.get('pallet_number')
            if not pallet_number:
                continue
            if pallet_number not in pallet_groups:
                pallet_groups[pallet_number] = {
                    'pallet_number': pallet_number,
                    'item_codes': [],
                    'po_number': item_data.get('po_number')
                }
            pallet_groups[pallet_number]['item_codes'].append(item_data['item_code'])

        weight_records = db.query(PalletWeight).filter(PalletWeight.shipment_code == shipment_code).all()
        weight_by_pallet = {record.pallet_number: record.weight for record in weight_records}

        pallets = [
            {
                'pallet_number': group['pallet_number'],
                'item_codes': group['item_codes'],
                'po_number': group['po_number'],
                'weight': weight_by_pallet.get(group['pallet_number'])
            }
            for group in sorted(pallet_groups.values(), key=lambda g: g['pallet_number'])
        ]

        return {
            'success': True,
            'shipment_code': shipment_code,
            'items': packing_slip_items,
            'pallets': pallets,
            'total_items': len(packing_slip_items),
            'note': 'Qty remaining uses customer order API fields: quantity - shipped'
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/shipments/finalized/list")
async def list_packing_slip_shipments(db: Session = Depends(get_db)):
    """
    List shipments available in shipment_boxes table
    """
    try:
        rows = (
            db.query(
                ShipmentBox.shipment_code,
                ShipmentBox.po_number,
                ShipmentBox.customer_name,
                ShipmentBox.shipping_address,
                ShipmentBox.job_number,
                ShipmentBox.delivery_date,
                func.max(ShipmentBox.finalized_at).label("finalized_at"),
                func.count(ShipmentBox.id).label("total_boxes")
            )
            .group_by(
                ShipmentBox.shipment_code,
                ShipmentBox.po_number,
                ShipmentBox.customer_name,
                ShipmentBox.shipping_address,
                ShipmentBox.job_number,
                ShipmentBox.delivery_date
            )
            .order_by(ShipmentBox.shipment_code)
            .all()
        )

        shipments = [
            {
                "shipment_code": r.shipment_code,
                "po_number": r.po_number,
                "customer_name": r.customer_name,
                "shipping_address": r.shipping_address,
                "job_number": r.job_number,
                "delivery_date": r.delivery_date,
                "finalized_at": r.finalized_at.isoformat() if r.finalized_at else None,
                "total_boxes": r.total_boxes
            }
            for r in rows
        ]

        return {"success": True, "shipments": shipments}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


"""
Label generation routes
"""
from fastapi import APIRouter, HTTPException, Depends, Body, UploadFile, File
from typing import Literal, Dict, List, Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
import json
import io
import re
import pandas as pd
from app.services.mrpeasy_client import mrpeasy_client
from app.config.database import get_db
from app.models import ShipmentBox, Label

class FinalizeShipmentRequest(BaseModel):
    pallet_number: Optional[str] = None
    product_configs: Dict = {}


class PastePackSizesRequest(BaseModel):
    text: str


router = APIRouter(prefix="/api/labels", tags=["labels"])


def _normalize_item_code(value) -> str:
    """
    Normalize item code variants so e.g. "15437-NUTS", "15437-NUT", "15437-nut"
    and "15437 - NUTS" all match the same underlying item code.
    """
    text = str(value or '').strip().upper()
    text = re.sub(r'\s+', '', text)  # "15437 - NUT" -> "15437-NUT"
    text = re.sub(r'NUTS$', 'NUT', text)  # treat plural NUTS suffix as NUT
    return text


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
                qty_booked = product.get('quantity_booked', 0)
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
            combined_groups[group_key]['total_quantity'] += product.get('quantity_booked', 0)
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
async def finalize_shipment_configuration(
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
            quantity_booked = product.get('quantity_booked', 0)
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


@router.get("/packing-slip/{shipment_code}")
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
        
        return {
            'success': True,
            'shipment_code': shipment_code,
            'items': packing_slip_items,
            'total_items': len(packing_slip_items),
            'note': 'Qty remaining uses customer order API fields: quantity - shipped'
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/packing-slip/shipments/list")
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


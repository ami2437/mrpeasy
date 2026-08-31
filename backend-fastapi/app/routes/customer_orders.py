from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.schemas import CustomerOrderResponse, CustomerOrderCreate, CustomerOrderUpdate
from app.services.crud import CustomerOrderService
from app.services.mrpeasy_client import mrpeasy_client
from app.models import User
from app.dependencies import get_current_active_user, require_permission

router = APIRouter(prefix="/api/customer-orders", tags=["customer-orders"])


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


def _get_invoice_match_key(product):
    article_id = product.get('article_id')
    if article_id not in (None, ''):
        return ('article_id', str(article_id))

    item_code = product.get('item_code')
    if item_code not in (None, ''):
        return ('item_code', str(item_code))

    return None


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    return value if isinstance(value, list) else []


def _allocate_invoice_quantities(products, order_invoices):
    allocations_by_index = {index: [] for index in range(len(products))}
    invoice_pools = {}

    for invoice in order_invoices:
        for invoice_product in invoice.get('products', []):
            match_key = _get_invoice_match_key(invoice_product)
            if not match_key:
                continue

            quantity = _to_number(invoice_product.get('quantity', 0), 0)
            if quantity <= 0:
                continue

            invoice_pools.setdefault(match_key, []).append({
                'invoice_code': invoice.get('code'),
                'invoice_status': invoice.get('status_txt'),
                'remaining_quantity': quantity
            })

    for index, product in enumerate(products):
        shipped_quantity = _to_number(product.get('shipped', 0), 0)
        if shipped_quantity <= 0:
            continue

        match_key = _get_invoice_match_key(product)
        if not match_key:
            continue

        remaining_to_allocate = shipped_quantity
        for invoice_entry in invoice_pools.get(match_key, []):
            if remaining_to_allocate <= 0:
                break

            available_quantity = invoice_entry['remaining_quantity']
            if available_quantity <= 0:
                continue

            allocated_quantity = min(remaining_to_allocate, available_quantity)
            allocations_by_index[index].append({
                'invoice_code': invoice_entry['invoice_code'],
                'invoice_status': invoice_entry['invoice_status'],
                'quantity_invoiced': allocated_quantity
            })
            invoice_entry['remaining_quantity'] -= allocated_quantity
            remaining_to_allocate -= allocated_quantity

    return allocations_by_index


@router.get("/", response_model=list[CustomerOrderResponse])
def list_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Get all customer orders from local database.
    Requires: read permission (all authenticated users)
    """
    orders = CustomerOrderService.get_orders(db, skip=skip, limit=limit)
    return orders


@router.get("/shipment-status")
def get_shipment_status_orders(
    filter_type: str = Query("all_active", description="Filter type: all_active, any_undelivered, not_shipped, partially_shipped"),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get customer orders filtered by shipment status with invoice information.
    
    Filter types:
    - all_active: All orders with status NOT 90 (Cancelled) and NOT 80 (Delivered)
    - any_undelivered: Orders with any undelivered items (0 shipped or partial shipped)
    - not_shipped: Orders with NO items shipped at all
    - partially_shipped: Orders with partially shipped items (0 < shipped < quantity)
    
    Returns order details with invoice information.
    Requires: read permission (all authenticated users)
    """
    try:
        # Fetch all customer orders
        all_orders = _as_list(mrpeasy_client.get_customer_orders())
        
        if not all_orders:
            return []
        
        # Debug logging removed after identifying job number field.
        
        # Fetch all invoices for matching
        all_invoices = _as_list(mrpeasy_client.get_invoices())
        
        # Fetch all shipments for delivery dates
        all_shipments = _as_list(mrpeasy_client.get_shipments())

        valid_filters = {"all_active", "any_undelivered", "not_shipped", "partially_shipped"}
        if filter_type not in valid_filters:
            raise HTTPException(status_code=400, detail=f"Invalid filter_type: {filter_type}")
        
        # Create invoice lookup by customer order ID
        invoices_by_order = {}
        for raw_invoice in all_invoices:
            invoice = _as_dict(raw_invoice)
            if not invoice:
                continue
            cust_ord_id = invoice.get('cust_ord_id')
            if cust_ord_id:
                if cust_ord_id not in invoices_by_order:
                    invoices_by_order[cust_ord_id] = []
                invoices_by_order[cust_ord_id].append({
                    'invoice_id': invoice.get('invoice_id'),
                    'code': invoice.get('code'),
                    'type': invoice.get('type_txt'),
                    'status': invoice.get('status'),
                    'status_txt': invoice.get('status_txt'),
                    'total_price': invoice.get('total_price'),
                    'total_price_cur': invoice.get('total_price_cur'),
                    'currency': invoice.get('currency'),
                    'created': invoice.get('created'),
                    'due_date': invoice.get('due_date'),
                    'products': _as_list(invoice.get('products'))
                })
        
        # Create shipment lookup by customer order ID
        shipments_by_order = {}
        for raw_shipment in all_shipments:
            shipment = _as_dict(raw_shipment)
            if not shipment:
                continue
            cust_ord_id = shipment.get('cust_ord_id')
            if cust_ord_id:
                if cust_ord_id not in shipments_by_order:
                    shipments_by_order[cust_ord_id] = []
                shipments_by_order[cust_ord_id].append({
                    'shipment_id': shipment.get('shipment_id'),
                    'code': shipment.get('code'),
                    'shipped_date': shipment.get('shipped_date'),
                    'expected_date': shipment.get('expected_date'),
                    'created': shipment.get('created')
                })
        
        filtered_orders = []
        
        for raw_order in all_orders:
            order = _as_dict(raw_order)
            if not order:
                continue
            status = order.get('status')
            
            # Pre-filter: exclude cancelled (90) and delivered (80) for all filter types
            if filter_type != "all_active":
                if status in [80, 90]:
                    continue
            else:
                # For all_active, only exclude cancelled and delivered
                if status in [80, 90]:
                    continue
            
            products = _as_list(order.get('products'))
            cust_ord_id = order.get('cust_ord_id')
            
            # Calculate shipment statistics
            total_items = len(products)
            fully_shipped_items = 0
            partially_shipped_items = 0
            not_shipped_items = 0
            total_shipped_value = 0
            total_remaining_value = 0
            
            order_invoices = invoices_by_order.get(cust_ord_id, [])
            invoice_allocations = _allocate_invoice_quantities(products, order_invoices)
            enriched_products = []
            
            for product_index, raw_product in enumerate(products):
                product = _as_dict(raw_product)
                if not product:
                    continue
                quantity = _to_number(product.get('quantity', 0), 0)
                shipped = _to_number(product.get('shipped', 0), 0)
                item_price = _to_number(product.get('item_price', 0), 0)
                
                # Determine item status
                if shipped == 0:
                    not_shipped_items += 1
                    item_status = 'Not Shipped'
                elif shipped >= quantity:
                    fully_shipped_items += 1
                    item_status = 'Fully Shipped'
                else:
                    partially_shipped_items += 1
                    item_status = 'Partially Shipped'
                
                # Calculate prices
                total_price = (item_price * quantity) if item_price and quantity else 0
                shipped_price = (item_price * shipped) if item_price and shipped else 0
                remaining_price = (item_price * (quantity - shipped)) if item_price and (quantity - shipped) else 0
                
                # Accumulate order-level values
                total_shipped_value += shipped_price
                total_remaining_value += remaining_price
                
                enriched_products.append({
                    'article_id': product.get('article_id'),
                    'item_code': product.get('item_code'),
                    'item_title': product.get('item_title'),
                    'box_info_per_item': product.get('description') or '',
                    'quantity': quantity,
                    'shipped': shipped,
                    'remaining': quantity - shipped,
                    'status': item_status,
                    'item_price': round(item_price, 2) if item_price else 0,
                    'total_price': round(total_price, 2) if total_price else 0,
                    'shipped_price': round(shipped_price, 2) if shipped_price else 0,
                    'remaining_price': round(remaining_price, 2) if remaining_price else 0,
                    'invoices': invoice_allocations.get(product_index, [])
                })
            
            # Apply filter logic
            include_order = False
            
            if filter_type == "all_active":
                # Already filtered above (status NOT 80 and NOT 90)
                include_order = True
                
            elif filter_type == "any_undelivered":
                # Orders with any undelivered items (not fully shipped)
                include_order = (not_shipped_items > 0 or partially_shipped_items > 0)
                
            elif filter_type == "not_shipped":
                # Orders with NO items shipped at all
                include_order = (not_shipped_items == total_items)
                
            elif filter_type == "partially_shipped":
                # Orders with at least one partially shipped item
                include_order = (partially_shipped_items > 0)
            
            if include_order:
                # Get all shipments for this order
                order_shipments = shipments_by_order.get(cust_ord_id, [])
                
                # Get the most recent shipment date
                shipment_dates = [s.get('shipped_date') for s in order_shipments if s.get('shipped_date')]
                latest_shipment_date = max(shipment_dates) if shipment_dates else None
                
                # Job number appears in custom_814 for customer orders.
                job_number = 'N/A'
                job_number_candidate = order.get('custom_814')
                if job_number_candidate is not None:
                    if isinstance(job_number_candidate, (int, float)):
                        if int(job_number_candidate) < 1000000000:
                            job_number = str(job_number_candidate)
                    elif isinstance(job_number_candidate, str):
                        trimmed = job_number_candidate.strip()
                        if trimmed:
                            lower_trimmed = trimmed.lower()
                            if lower_trimmed not in {"partial", "complete", "open"}:
                                if not (trimmed.isdigit() and int(trimmed) >= 1000000000):
                                    job_number = trimmed
                
                # Try multiple fields for delivery date
                delivery_date = (order.get('actual_delivery_date') or 
                                order.get('delivery_date') or 
                                order.get('expected_delivery_date'))
                
                filtered_orders.append({
                    'cust_ord_id': cust_ord_id,
                    'code': order.get('code'),
                    'reference': order.get('reference'),
                    'customer_name': order.get('customer_name'),
                    'customer_id': order.get('customer_id'),
                    'status': status,
                    'status_txt': order.get('status_txt'),
                    'invoice_status': order.get('invoice_status'),
                    'invoice_status_txt': order.get('invoice_status_txt'),
                    'total_price': order.get('total_price'),
                    'currency': order.get('currency'),
                    'delivery_date': delivery_date,
                    'actual_delivery_date': order.get('actual_delivery_date'),
                    'created': order.get('created'),
                    'modified': order.get('modified'),
                    'confirmed_at': order.get('confirmed_at'),
                    'job_number': job_number,
                    'custom_218': order.get('custom_218'),
                    'custom_748': order.get('custom_748'),
                    'custom_775': order.get('custom_775'),
                    'custom_814': order.get('custom_814'),
                    'shipped_value': round(total_shipped_value, 2) if total_shipped_value else 0,
                    'remaining_value': round(total_remaining_value, 2) if total_remaining_value else 0,
                    'shipments': order_shipments,
                    'latest_shipment_date': latest_shipment_date,
                    'products': enriched_products,
                    'shipment_summary': {
                        'total_items': total_items,
                        'fully_shipped': fully_shipped_items,
                        'partially_shipped': partially_shipped_items,
                        'not_shipped': not_shipped_items
                    },
                    'invoices': order_invoices,
                    'invoice_count': len(order_invoices)
                })
        
        return filtered_orders
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch orders by shipment status: {str(e)}"
        )


@router.get("/{order_id}", response_model=CustomerOrderResponse)
def get_order(
    order_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Get specific customer order.
    Requires: read permission (all authenticated users)
    """
    order = CustomerOrderService.get_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.put("/{order_id}", response_model=CustomerOrderResponse)
def update_order(
    order_id: int,
    order: CustomerOrderUpdate,
    current_user: User = Depends(require_permission("write")),
    db: Session = Depends(get_db)
):
    """
    Update customer order in LOCAL DATABASE ONLY.
    This does NOT modify data in MRPeasy - use for custom portal data only.
    Requires: write permission (admin, editor only)
    """
    db_order = CustomerOrderService.update_order(db, order_id, order)
    if not db_order:
        raise HTTPException(status_code=404, detail="Order not found")
    return db_order


@router.delete("/{order_id}", status_code=204)
def delete_order(
    order_id: int,
    current_user: User = Depends(require_permission("delete")),
    db: Session = Depends(get_db)
):
    """
    Delete customer order from LOCAL DATABASE ONLY.
    This does NOT delete data in MRPeasy - local database cleanup only.
    Requires: delete permission (admin only)
    """
    if not CustomerOrderService.delete_order(db, order_id):
        raise HTTPException(status_code=404, detail="Order not found")


@router.get("/mrpeasy/partially-shipped")
def get_partially_shipped_orders(
    current_user: User = Depends(get_current_active_user)
):
    """
    Get customer orders from MRPeasy API where at least one item is partially shipped.
    Partially shipped means: 0 < shipped < quantity for any item in the order.
    Returns full order details including all products.
    Requires: read permission (all authenticated users)
    """
    try:
        # Fetch all customer orders from MRPeasy
        all_orders = mrpeasy_client.get_customer_orders()
        
        if not all_orders:
            return []
        
        # Filter orders with partially shipped items
        partially_shipped_orders = []
        
        for order in all_orders:
            products = order.get('products', [])
            
            # Check if any product is partially shipped
            has_partial_shipment = False
            for product in products:
                quantity = product.get('quantity', 0)
                shipped = product.get('shipped', 0)
                
                # Check if partially shipped: 0 < shipped < quantity
                if 0 < shipped < quantity:
                    has_partial_shipment = True
                    break
            
            # If order has at least one partially shipped item, include it
            if has_partial_shipment:
                partially_shipped_orders.append(order)
        
        return partially_shipped_orders
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch partially shipped orders: {str(e)}"
        )


@router.get("/mrpeasy/partially-shipped-with-invoices")
def get_partially_shipped_orders_with_invoices(
    current_user: User = Depends(get_current_active_user)
):
    """
    Get customer orders with partially shipped items AND their associated invoices.
    Returns order details + invoice data for each partially shipped order.
    Requires: read permission (all authenticated users)
    """
    try:
        # Fetch all customer orders from MRPeasy
        all_orders = mrpeasy_client.get_customer_orders()
        
        if not all_orders:
            return []
        
        # Fetch all invoices from MRPeasy
        all_invoices = mrpeasy_client.get_invoices()
        
        # Filter orders with partially shipped items and attach invoice data
        result = []
        
        for order in all_orders:
            products = order.get('products', [])
            
            # Check if any product is partially shipped
            has_partial_shipment = False
            partially_shipped_items = []
            
            for product in products:
                quantity = product.get('quantity', 0)
                shipped = product.get('shipped', 0)
                
                # Check if partially shipped: 0 < shipped < quantity
                if 0 < shipped < quantity:
                    has_partial_shipment = True
                    partially_shipped_items.append({
                        'item_code': product.get('item_code'),
                        'item_title': product.get('item_title'),
                        'box_info_per_item': product.get('description') or '',
                        'quantity': quantity,
                        'shipped': shipped,
                        'remaining': quantity - shipped
                    })
            
            # If order has at least one partially shipped item, find its invoices
            if has_partial_shipment:
                order_id = order.get('cust_ord_id')
                
                # Find invoices related to this customer order
                related_invoices = []
                if all_invoices:
                    for invoice in all_invoices:
                        if invoice.get('cust_ord_id') == order_id:
                            related_invoices.append({
                                'invoice_id': invoice.get('invoice_id'),
                                'code': invoice.get('code'),
                                'type': invoice.get('type_txt'),
                                'status': invoice.get('status_txt'),
                                'total_price': invoice.get('total_price'),
                                'total_price_cur': invoice.get('total_price_cur'),
                                'currency': invoice.get('currency'),
                                'created': invoice.get('created'),
                                'due_date': invoice.get('due_date')
                            })
                
                result.append({
                    'order': {
                        'cust_ord_id': order.get('cust_ord_id'),
                        'code': order.get('code'),
                        'reference': order.get('reference'),
                        'customer_name': order.get('customer_name'),
                        'status': order.get('status_txt'),
                        'total_price': order.get('total_price'),
                        'currency': order.get('currency')
                    },
                    'partially_shipped_items': partially_shipped_items,
                    'invoices': related_invoices,
                    'invoice_count': len(related_invoices)
                })
        
        return result
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch partially shipped orders with invoices: {str(e)}"
        )


@router.get("/mrpeasy/uninvoiced-shipments")
def get_uninvoiced_shipments(
    current_user: User = Depends(get_current_active_user)
):
    """
    Get all customer orders with shipped items but NO or PARTIAL invoices.
    Uses invoice_status field for efficient filtering:
    - invoice_status = 30: Fully invoiced (excluded)
    - invoice_status = 20: Partially invoiced (check against invoice data)
    - invoice_status = 10: Not invoiced (apply partial/uninvoiced logic)
    
    Separates results into partially shipped vs fully shipped orders.
    Requires: read permission (all authenticated users)
    """
    try:
        # Fetch all customer orders from MRPeasy
        all_orders = mrpeasy_client.get_customer_orders()
        
        if not all_orders:
            return {
                "partially_shipped_uninvoiced": [],
                "partially_invoiced": [],
                "fully_shipped_uninvoiced": [],
                "total_uninvoiced_orders": 0
            }
        
        partially_shipped_uninvoiced = []
        partially_invoiced_orders = []
        fully_shipped_uninvoiced = []
        
        # First, filter out fully invoiced orders (invoice_status = 30)
        # Then process partially invoiced (invoice_status = 20) and not invoiced (invoice_status = 10)
        
        for order in all_orders:
            invoice_status = order.get('invoice_status')
            
            # Skip fully invoiced orders
            if invoice_status == 30:
                continue
            
            products = order.get('products', [])
            
            # Track shipped items and shipment status
            has_shipped_items = False
            is_partially_shipped = False
            shipped_items = []
            
            for product in products:
                quantity = product.get('quantity', 0)
                shipped = product.get('shipped', 0)
                
                if shipped > 0:
                    has_shipped_items = True
                    
                    # Check if partially shipped: 0 < shipped < quantity
                    if shipped < quantity:
                        is_partially_shipped = True
                    
                    shipped_items.append({
                        'item_code': product.get('item_code'),
                        'item_title': product.get('item_title'),
                        'box_info_per_item': product.get('description') or '',
                        'quantity': quantity,
                        'shipped': shipped,
                        'remaining': quantity - shipped,
                        'fulfillment_status': 'partial' if shipped < quantity else 'complete'
                    })
            
            # Only process orders with shipped items
            if not has_shipped_items:
                continue
            
            order_data = {
                'order': {
                    'cust_ord_id': order.get('cust_ord_id'),
                    'code': order.get('code'),
                    'reference': order.get('reference'),
                    'customer_name': order.get('customer_name'),
                    'status': order.get('status_txt'),
                    'invoice_status': invoice_status,
                    'total_price': order.get('total_price'),
                    'currency': order.get('currency')
                },
                'shipped_items': shipped_items,
                'total_shipped_items': len(shipped_items)
            }
            
            # Categorize based on invoice_status
            if invoice_status == 20:
                # Partially invoiced - needs attention
                order_data['needs_invoice'] = True
                order_data['invoice_status_text'] = 'Partially Invoiced'
                partially_invoiced_orders.append(order_data)
                
            elif invoice_status == 10:
                # Not invoiced - categorize by shipment status
                order_data['needs_invoice'] = True
                order_data['invoice_status_text'] = 'Not Invoiced'
                
                if is_partially_shipped:
                    partially_shipped_uninvoiced.append(order_data)
                else:
                    # Fully shipped but not invoiced
                    fully_shipped_uninvoiced.append(order_data)
        
        return {
            "partially_shipped_uninvoiced": partially_shipped_uninvoiced,
            "partially_shipped_count": len(partially_shipped_uninvoiced),
            "partially_invoiced": partially_invoiced_orders,
            "partially_invoiced_count": len(partially_invoiced_orders),
            "fully_shipped_uninvoiced": fully_shipped_uninvoiced,
            "fully_shipped_count": len(fully_shipped_uninvoiced),
            "total_uninvoiced_orders": len(partially_shipped_uninvoiced) + len(partially_invoiced_orders) + len(fully_shipped_uninvoiced)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch uninvoiced shipments: {str(e)}"
        )

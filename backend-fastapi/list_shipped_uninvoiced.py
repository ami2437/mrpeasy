import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()

# Fetch all customer orders
all_orders = client.get_customer_orders()

# Fetch all invoices
all_invoices = client.get_invoices()

# Build invoice item map: {cust_ord_id: {item_code: total_invoiced_qty}}
invoice_items_map = {}
if all_invoices:
    for invoice in all_invoices:
        cust_ord_id = invoice.get('cust_ord_id')
        if not cust_ord_id:
            continue
        
        if cust_ord_id not in invoice_items_map:
            invoice_items_map[cust_ord_id] = {}
        
        # Get products from invoice
        invoice_products = invoice.get('products', [])
        for product in invoice_products:
            item_code = product.get('item_code')
            quantity = product.get('quantity', 0)
            
            if item_code:
                if item_code not in invoice_items_map[cust_ord_id]:
                    invoice_items_map[cust_ord_id][item_code] = 0
                invoice_items_map[cust_ord_id][item_code] += quantity

shipped_uninvoiced_orders = []

# Process orders with invoice_status '10' or '20'
for order in all_orders:
    invoice_status = order.get('invoice_status')
    
    # Only process not invoiced ('10') or partially invoiced ('20') orders
    if str(invoice_status) not in ['10', '20']:
        continue
    
    cust_ord_id = order.get('cust_ord_id')
    products = order.get('products', [])
    
    uninvoiced_items = []
    
    # Check each product for shipped but not invoiced quantities
    for product in products:
        item_code = product.get('item_code')
        shipped_qty = product.get('shipped', 0)
        total_qty = product.get('quantity', 0)
        
        # Skip items with no shipments
        if shipped_qty <= 0:
            continue
        
        # Get invoiced quantity for this item in this order
        invoiced_qty = 0
        if cust_ord_id in invoice_items_map and item_code in invoice_items_map[cust_ord_id]:
            invoiced_qty = invoice_items_map[cust_ord_id][item_code]
        
        # Calculate uninvoiced shipped quantity
        uninvoiced_shipped_qty = shipped_qty - invoiced_qty
        
        # If there's uninvoiced shipped quantity, add to list
        if uninvoiced_shipped_qty > 0:
            uninvoiced_items.append({
                'item_code': item_code,
                'shipped_quantity': shipped_qty,
                'invoiced_quantity': invoiced_qty,
                'uninvoiced_shipped_quantity': uninvoiced_shipped_qty
            })
    
    # If order has uninvoiced shipped items, add to results
    if uninvoiced_items:
        shipped_uninvoiced_orders.append({
            'code': order.get('code'),
            'cust_ord_id': cust_ord_id,
            'customer_name': order.get('customer_name'),
            'reference': order.get('reference'),
            'invoice_status': invoice_status,
            'uninvoiced_items_count': len(uninvoiced_items)
        })

print("="*80)
print("CUSTOMER ORDERS WITH SHIPPED BUT UNINVOICED ITEMS")
print("="*80)
print(f"Total orders captured: {len(shipped_uninvoiced_orders)}\n")

if shipped_uninvoiced_orders:
    print("Order Codes:")
    for order in shipped_uninvoiced_orders:
        status_text = 'Not Invoiced' if order['invoice_status'] == '10' else 'Partially Invoiced'
        print(f"  {order['code']} (ID: {order['cust_ord_id']}) - {order['customer_name']} - {status_text} - {order['uninvoiced_items_count']} items")
    
    print(f"\nSimple list of order codes:")
    order_codes = [o['code'] for o in shipped_uninvoiced_orders]
    print(order_codes)
else:
    print("No orders with shipped uninvoiced items found.")

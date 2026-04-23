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
            quantity = product.get('quantity', 0) or 0
            
            if item_code:
                if item_code not in invoice_items_map[cust_ord_id]:
                    invoice_items_map[cust_ord_id][item_code] = 0
                invoice_items_map[cust_ord_id][item_code] += quantity

orders_with_discrepancies = []

# Process orders with invoice_status '10' or '20'
for order in all_orders:
    invoice_status = order.get('invoice_status')
    
    # Only process not invoiced ('10') or partially invoiced ('20') orders
    if str(invoice_status) not in ['10', '20']:
        continue
    
    cust_ord_id = order.get('cust_ord_id')
    products = order.get('products', [])
    
    discrepancy_items = []
    
    # Check each product for invoicing discrepancies
    for product in products:
        item_code = product.get('item_code')
        shipped_qty = product.get('shipped', 0) or 0
        total_qty = product.get('quantity', 0) or 0
        
        # Skip items with no shipments
        if shipped_qty <= 0:
            continue
        
        # Get invoiced quantity for this item in this order
        invoiced_qty = 0
        if cust_ord_id in invoice_items_map and item_code in invoice_items_map[cust_ord_id]:
            invoiced_qty = invoice_items_map[cust_ord_id][item_code]
        
        # Calculate discrepancy: shipped - invoiced
        discrepancy = shipped_qty - invoiced_qty
        
        # Flag ANY discrepancy (positive or negative)
        if discrepancy != 0:
            discrepancy_items.append({
                'item_code': item_code,
                'shipped_qty': shipped_qty,
                'invoiced_qty': invoiced_qty,
                'discrepancy': discrepancy,
                'type': 'under_invoiced' if discrepancy > 0 else 'over_invoiced'
            })
    
    # If order has discrepancy items, add to results
    if discrepancy_items:
        orders_with_discrepancies.append({
            'code': order.get('code'),
            'cust_ord_id': cust_ord_id,
            'customer': order.get('customer_name'),
            'invoice_status': invoice_status,
            'discrepancies': discrepancy_items
        })

print("="*80)
print("ORDERS WITH INVOICING DISCREPANCIES")
print("="*80)
print(f"Total orders: {len(orders_with_discrepancies)}\n")

# Show all orders
for idx, order in enumerate(orders_with_discrepancies, 1):
    status_text = 'Not Invoiced' if order['invoice_status'] == '10' else 'Partially Invoiced'
    print(f"{idx}. {order['code']} (ID: {order['cust_ord_id']}) - {order['customer']} - {status_text}")
    
    under_count = sum(1 for d in order['discrepancies'] if d['type'] == 'under_invoiced')
    over_count = sum(1 for d in order['discrepancies'] if d['type'] == 'over_invoiced')
    
    print(f"   Under-invoiced items: {under_count}, Over-invoiced items: {over_count}")
    
    for disc in order['discrepancies']:
        symbol = "⚠️ " if disc['type'] == 'under_invoiced' else "🔴"
        print(f"   {symbol} {disc['item_code']}: shipped={disc['shipped_qty']}, invoiced={disc['invoiced_qty']}, diff={disc['discrepancy']} ({disc['type']})")
    print()

# Find the new order (C89077)
c89077_in_list = any(o['code'] == 'C89077' for o in orders_with_discrepancies)
print("="*80)
print("WHY COUNT INCREASED FROM 21 TO 22:")
print("="*80)
if c89077_in_list:
    print("✓ C89077 is NOW captured (it was excluded before)")
    print("  Reason: Changed logic from 'uninvoiced_shipped_qty > 0' to 'discrepancy != 0'")
    print("  Before: Only captured under-invoiced items (missing invoices)")
    print("  After: Captures ALL discrepancies (under-invoiced AND over-invoiced)")
    print("\n  C89077 has OVER-INVOICED items (invoice exceeds shipment)")
else:
    print("❌ C89077 is still not in the list")
    
print("="*80)

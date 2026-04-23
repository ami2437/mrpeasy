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

not_invoiced_orders = []
over_invoiced_orders = []

# Process orders with invoice_status '10' or '20'
for order in all_orders:
    invoice_status = order.get('invoice_status')
    
    # Only process not invoiced ('10') or partially invoiced ('20') orders
    if str(invoice_status) not in ['10', '20']:
        continue
    
    cust_ord_id = order.get('cust_ord_id')
    products = order.get('products', [])
    
    not_invoiced_items = []
    over_invoiced_items = []
    
    # Check each product for invoicing discrepancies
    for product in products:
        item_code = product.get('item_code')
        shipped_qty = product.get('shipped', 0) or 0
        
        # Skip items with no shipments
        if shipped_qty <= 0:
            continue
        
        # Get invoiced quantity for this item in this order (SUMMED across all invoices)
        invoiced_qty = 0
        if cust_ord_id in invoice_items_map and item_code in invoice_items_map[cust_ord_id]:
            invoiced_qty = invoice_items_map[cust_ord_id][item_code]
        
        # Calculate discrepancy: shipped - invoiced
        discrepancy = shipped_qty - invoiced_qty
        
        # Categorize: only 2 categories now
        if discrepancy > 0:
            # Not fully invoiced (includes both zero and partial invoices)
            not_invoiced_items.append({
                'item_code': item_code,
                'shipped_qty': shipped_qty,
                'invoiced_qty': invoiced_qty,
                'discrepancy': discrepancy
            })
        elif discrepancy < 0:
            # Over-invoiced
            over_invoiced_items.append({
                'item_code': item_code,
                'shipped_qty': shipped_qty,
                'invoiced_qty': invoiced_qty,
                'discrepancy': discrepancy
            })
    
    # Add to respective category lists
    order_info = {
        'code': order.get('code'),
        'cust_ord_id': cust_ord_id,
        'customer': order.get('customer_name'),
        'invoice_status': invoice_status
    }
    
    if not_invoiced_items:
        not_invoiced_orders.append({**order_info, 'items': not_invoiced_items})
    if over_invoiced_items:
        over_invoiced_orders.append({**order_info, 'items': over_invoiced_items})

print("="*80)
print("FINAL CATEGORIZATION (2 CATEGORIES)")
print("="*80)
print()

# NOT INVOICED / UNDER-INVOICED (shipped > invoiced)
print("📋 NOT INVOICED: Shipped but not fully invoiced")
print("-" * 80)
print(f"Total orders: {len(not_invoiced_orders)}\n")
for idx, order in enumerate(not_invoiced_orders[:5], 1):  # Show first 5
    print(f"{idx}. {order['code']} - {order['customer']}")
    for item in order['items'][:2]:  # Show first 2 items
        status = "no invoice" if item['invoiced_qty'] == 0 else f"partial ({item['invoiced_qty']} invoiced)"
        print(f"   • {item['item_code']}: shipped={item['shipped_qty']}, {status}, missing={item['discrepancy']}")
    if len(order['items']) > 2:
        print(f"   ... and {len(order['items']) - 2} more items")
    print()
if len(not_invoiced_orders) > 5:
    print(f"... and {len(not_invoiced_orders) - 5} more orders\n")

print()
print("="*80)
print("🔴 OVER-INVOICED: Invoice exceeds shipment")
print("-" * 80)
print(f"Total orders: {len(over_invoiced_orders)}\n")
for idx, order in enumerate(over_invoiced_orders, 1):
    print(f"{idx}. {order['code']} - {order['customer']}")
    for item in order['items']:
        print(f"   • {item['item_code']}: shipped={item['shipped_qty']}, invoiced={item['invoiced_qty']}, excess={abs(item['discrepancy'])}")
    print()

print()
print("="*80)
print("SUMMARY")
print("="*80)
print(f"Not Invoiced Orders: {len(not_invoiced_orders)}")
print(f"Over-Invoiced Orders: {len(over_invoiced_orders)}")
print(f"Total: {len(not_invoiced_orders) + len(over_invoiced_orders)}")
print("="*80)

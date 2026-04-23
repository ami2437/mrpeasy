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
under_invoiced_orders = []
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
    under_invoiced_items = []
    over_invoiced_items = []
    
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
        
        # Categorize by discrepancy type
        if discrepancy != 0:
            item_data = {
                'item_code': item_code,
                'shipped_qty': shipped_qty,
                'invoiced_qty': invoiced_qty,
                'discrepancy': discrepancy
            }
            
            if invoiced_qty == 0:
                not_invoiced_items.append(item_data)
            elif discrepancy > 0:
                under_invoiced_items.append(item_data)
            else:
                over_invoiced_items.append(item_data)
    
    # Add to respective category lists
    order_info = {
        'code': order.get('code'),
        'cust_ord_id': cust_ord_id,
        'customer': order.get('customer_name'),
        'invoice_status': invoice_status
    }
    
    if not_invoiced_items:
        not_invoiced_orders.append({**order_info, 'items': not_invoiced_items})
    if under_invoiced_items:
        under_invoiced_orders.append({**order_info, 'items': under_invoiced_items})
    if over_invoiced_items:
        over_invoiced_orders.append({**order_info, 'items': over_invoiced_items})

print("="*80)
print("INVOICING DISCREPANCIES - CATEGORIZED")
print("="*80)
print()

# NOT INVOICED
print("📋 CATEGORY 1: NOT INVOICED (Shipped but no invoice created)")
print("-" * 80)
print(f"Total orders: {len(not_invoiced_orders)}\n")
for idx, order in enumerate(not_invoiced_orders, 1):
    print(f"{idx}. {order['code']} - {order['customer']}")
    print(f"   Items with no invoice: {len(order['items'])}")
    for item in order['items'][:3]:  # Show first 3 items
        print(f"   • {item['item_code']}: shipped={item['shipped_qty']}, invoiced=0")
    if len(order['items']) > 3:
        print(f"   ... and {len(order['items']) - 3} more items")
    print()

print()
print("="*80)
print("⚠️  CATEGORY 2: UNDER-INVOICED (Partial invoices created)")
print("-" * 80)
print(f"Total orders: {len(under_invoiced_orders)}\n")
if under_invoiced_orders:
    for idx, order in enumerate(under_invoiced_orders, 1):
        print(f"{idx}. {order['code']} - {order['customer']}")
        print(f"   Items partially invoiced: {len(order['items'])}")
        for item in order['items']:
            print(f"   • {item['item_code']}: shipped={item['shipped_qty']}, invoiced={item['invoiced_qty']}, missing={item['discrepancy']}")
        print()
else:
    print("None found.\n")

print()
print("="*80)
print("🔴 CATEGORY 3: OVER-INVOICED (Invoice exceeds shipment)")
print("-" * 80)
print(f"Total orders: {len(over_invoiced_orders)}\n")
if over_invoiced_orders:
    for idx, order in enumerate(over_invoiced_orders, 1):
        print(f"{idx}. {order['code']} - {order['customer']}")
        print(f"   Items over-invoiced: {len(order['items'])}")
        for item in order['items']:
            print(f"   • {item['item_code']}: shipped={item['shipped_qty']}, invoiced={item['invoiced_qty']}, excess={abs(item['discrepancy'])}")
        print()
else:
    print("None found.\n")

print()
print("="*80)
print("SUMMARY")
print("="*80)
print(f"Not Invoiced Orders: {len(not_invoiced_orders)}")
print(f"Under-Invoiced Orders: {len(under_invoiced_orders)}")
print(f"Over-Invoiced Orders: {len(over_invoiced_orders)}")
print(f"Total Orders with Discrepancies: {len(set([o['code'] for o in not_invoiced_orders + under_invoiced_orders + over_invoiced_orders]))}")
print("="*80)

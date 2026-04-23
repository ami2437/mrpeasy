import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()

# Fetch orders
orders = client.get_customer_orders()
print(f"Total orders: {len(orders)}")

# Count by invoice_status
status_counts = {}
for order in orders:
    status = order.get('invoice_status')
    if status not in status_counts:
        status_counts[status] = 0
    status_counts[status] += 1

print("\nInvoice Status Breakdown:")
for status, count in sorted(status_counts.items()):
    print(f"  Status {status}: {count} orders")

# Check orders with invoice_status 10 or 20
print("\n" + "="*80)
print("ORDERS WITH INVOICE_STATUS 10 or 20:")
print("="*80)

target_orders = [o for o in orders if o.get('invoice_status') in [10, 20]]
print(f"Found {len(target_orders)} orders with invoice_status 10 or 20\n")

for order in target_orders[:5]:  # Show first 5
    cust_ord_id = order.get('cust_ord_id')
    code = order.get('code')
    invoice_status = order.get('invoice_status')
    products = order.get('products', [])
    
    print(f"Order {cust_ord_id} ({code}) - Invoice Status: {invoice_status}")
    
    shipped_items = []
    for p in products:
        shipped = p.get('shipped', 0)
        if shipped > 0:
            shipped_items.append({
                'item_code': p.get('item_code'),
                'quantity': p.get('quantity', 0),
                'shipped': shipped
            })
    
    if shipped_items:
        print(f"  Shipped items: {len(shipped_items)}")
        for item in shipped_items[:3]:
            print(f"    {item['item_code']}: qty={item['quantity']}, shipped={item['shipped']}")
    else:
        print(f"  No shipped items")
    print()

# Fetch invoices
print("\n" + "="*80)
print("INVOICES:")
print("="*80)
invoices = client.get_invoices()
print(f"Total invoices: {len(invoices)}\n")

# Build invoice map
invoice_map = {}
for inv in invoices:
    cust_ord_id = inv.get('cust_ord_id')
    if cust_ord_id:
        if cust_ord_id not in invoice_map:
            invoice_map[cust_ord_id] = []
        invoice_map[cust_ord_id].append(inv)

print(f"Orders with invoices: {len(invoice_map)}")

# Check if target orders have invoices
print("\nChecking if target orders (invoice_status 10/20) have invoice records:")
for order in target_orders[:5]:
    cust_ord_id = order.get('cust_ord_id')
    code = order.get('code')
    invoice_status = order.get('invoice_status')
    
    has_invoice = cust_ord_id in invoice_map
    print(f"Order {cust_ord_id} ({code}) - Invoice Status: {invoice_status} - Has Invoice: {has_invoice}")

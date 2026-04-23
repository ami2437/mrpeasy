import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()

# Get order C89077
all_orders = client.get_customer_orders()
order_c89077 = None
for order in all_orders:
    if order.get('code') == 'C89077':
        order_c89077 = order
        break

if not order_c89077:
    print("Order C89077 not found!")
    sys.exit(1)

print("="*80)
print("ORDER C89077 ANALYSIS")
print("="*80)

cust_ord_id = order_c89077.get('cust_ord_id')
invoice_status = order_c89077.get('invoice_status')
print(f"Customer Order ID: {cust_ord_id}")
print(f"Code: {order_c89077.get('code')}")
print(f"Customer: {order_c89077.get('customer_name')}")
print(f"Invoice Status: {invoice_status} ({type(invoice_status).__name__})")
print(f"Status: {order_c89077.get('status_txt')}")

# Check products
products = order_c89077.get('products', [])
print(f"\nProducts ({len(products)} items):")
for p in products:
    qty = p.get('quantity', 0)
    shipped = p.get('shipped', 0)
    print(f"  {p.get('item_code')}: qty={qty}, shipped={shipped}")

# Check if it passes filter 1: invoice_status in ['10', '20']
passes_filter_1 = str(invoice_status) in ['10', '20']
print(f"\nFilter 1 (invoice_status in ['10', '20']): {passes_filter_1}")

# Check if it has shipped items
shipped_items = [p for p in products if p.get('shipped', 0) > 0]
has_shipped_items = len(shipped_items) > 0
print(f"Filter 2 (has shipped items): {has_shipped_items} ({len(shipped_items)} items)")

# Now check invoices
all_invoices = client.get_invoices()
print(f"\n--- Checking Invoices ---")
print(f"Total invoices in system: {len(all_invoices)}")

# Find invoices for this order
order_invoices = [inv for inv in all_invoices if inv.get('cust_ord_id') == cust_ord_id]
print(f"Invoices for order {cust_ord_id}: {len(order_invoices)}")

if order_invoices:
    print("\nInvoice details:")
    for inv in order_invoices:
        print(f"  Invoice {inv.get('invoice_id')} ({inv.get('code')})")
        print(f"    Status: {inv.get('status_txt')}")
        print(f"    Total: {inv.get('total_price')} {inv.get('currency')}")
        inv_products = inv.get('products', [])
        print(f"    Products ({len(inv_products)} items):")
        for p in inv_products:
            print(f"      {p.get('item_code')}: qty={p.get('quantity', 0)}")

# Build invoice map for this order
invoice_items_map = {}
for invoice in order_invoices:
    invoice_products = invoice.get('products', [])
    for product in invoice_products:
        item_code = product.get('item_code')
        quantity = product.get('quantity', 0)
        
        if item_code:
            if item_code not in invoice_items_map:
                invoice_items_map[item_code] = 0
            invoice_items_map[item_code] += quantity

print(f"\n--- Uninvoiced Calculation ---")
for product in products:
    item_code = product.get('item_code')
    shipped_qty = product.get('shipped', 0)
    
    if shipped_qty > 0:
        invoiced_qty = invoice_items_map.get(item_code, 0)
        uninvoiced_shipped_qty = shipped_qty - invoiced_qty
        
        print(f"{item_code}:")
        print(f"  Shipped: {shipped_qty}")
        print(f"  Invoiced: {invoiced_qty}")
        print(f"  Uninvoiced: {uninvoiced_shipped_qty}")
        print(f"  Would be captured: {uninvoiced_shipped_qty > 0}")

print("\n" + "="*80)
print("CONCLUSION:")
if not passes_filter_1:
    print(f"❌ Order C89077 was NOT captured because invoice_status '{invoice_status}' is not in ['10', '20']")
elif not has_shipped_items:
    print(f"❌ Order C89077 was NOT captured because it has no shipped items")
elif len(order_invoices) > 0:
    has_uninvoiced = False
    for product in products:
        if product.get('shipped', 0) > 0:
            invoiced_qty = invoice_items_map.get(product.get('item_code'), 0)
            if product.get('shipped', 0) > invoiced_qty:
                has_uninvoiced = True
                break
    
    if has_uninvoiced:
        print(f"✓ Order C89077 SHOULD be captured (has uninvoiced shipped items)")
    else:
        print(f"❌ Order C89077 was NOT captured because all shipped items are already invoiced")
else:
    print(f"✓ Order C89077 SHOULD be captured")
print("="*80)

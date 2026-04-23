import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()

# Get all invoices
all_invoices = client.get_invoices()

print("="*80)
print("SEARCHING FOR ALL INVOICES WITH ITEM 39280")
print("="*80)

invoices_with_39280 = []
for inv in all_invoices:
    products = inv.get('products', [])
    for prod in products:
        if prod.get('item_code') == '39280':
            invoices_with_39280.append({
                'invoice_id': inv.get('invoice_id'),
                'invoice_code': inv.get('code'),
                'cust_ord_id': inv.get('cust_ord_id'),
                'status': inv.get('status_txt'),
                'quantity': prod.get('quantity', 0) or 0
            })
            break

print(f"Found {len(invoices_with_39280)} invoices with item 39280:\n")

for inv in invoices_with_39280:
    print(f"Invoice {inv['invoice_code']} (ID: {inv['invoice_id']})")
    print(f"  Customer Order ID: {inv['cust_ord_id']}")
    print(f"  Status: {inv['status']}")
    print(f"  Item 39280 Quantity: {inv['quantity']}")
    print()

# Group by customer order ID
from collections import defaultdict
by_order = defaultdict(list)
for inv in invoices_with_39280:
    by_order[inv['cust_ord_id']].append(inv)

print("="*80)
print("GROUPED BY CUSTOMER ORDER ID")
print("="*80)
for cust_ord_id, invoices in by_order.items():
    total_qty = sum(inv['quantity'] for inv in invoices)
    print(f"\nCustomer Order ID {cust_ord_id}:")
    print(f"  Number of invoices: {len(invoices)}")
    print(f"  Total quantity: {total_qty}")
    for inv in invoices:
        print(f"    - Invoice {inv['invoice_code']}: {inv['quantity']} units")

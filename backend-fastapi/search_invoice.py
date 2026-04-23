import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()

# Get all invoices
all_invoices = client.get_invoices()

print("="*80)
print("SEARCHING FOR INVOICE: Inv-5464545")
print("="*80)

# Search by invoice code
found = None
for inv in all_invoices:
    if inv.get('code') == 'Inv-5464545':
        found = inv
        break

if found:
    print(f"\n✓ FOUND!\n")
    print(f"Invoice Code: {found.get('code')}")
    print(f"Invoice ID: {found.get('invoice_id')}")
    print(f"Customer Order ID: {found.get('cust_ord_id')}")
    print(f"Status: {found.get('status_txt')}")
    print(f"Date: {found.get('date')}")
    print(f"Total Price: {found.get('total_price')} {found.get('currency')}")
    print(f"\nProducts:")
    for prod in found.get('products', []):
        item_code = prod.get('item_code')
        qty = prod.get('quantity', 0) or 0
        price = prod.get('price', 0) or 0
        print(f"  - {item_code}: quantity={qty}, price={price}")
    
    # Get the order for this invoice
    print(f"\n{'='*80}")
    print("CORRESPONDING ORDER")
    print(f"{'='*80}")
    
    all_orders = client.get_customer_orders()
    for order in all_orders:
        if order.get('cust_ord_id') == found.get('cust_ord_id'):
            print(f"Order Code: {order.get('code')}")
            print(f"Customer: {order.get('customer_name')}")
            print(f"Status: {order.get('status_txt')}")
            print(f"Invoice Status: {order.get('invoice_status')}")
            print(f"\nShipped Items:")
            for prod in order.get('products', []):
                print(f"  - {prod.get('item_code')}: {prod.get('shipped')} units")
            break
else:
    print(f"\n✗ NOT FOUND")
    print(f"\nSearching for similar invoice codes containing '5464545'...\n")
    
    found_similar = False
    for inv in all_invoices:
        if '5464545' in str(inv.get('code', '')):
            print(f"  - {inv.get('code')} (ID: {inv.get('invoice_id')})")
            found_similar = True
    
    if not found_similar:
        print(f"  No invoices found with '5464545' in the code")
    
    print(f"\nTotal invoices in system: {len(all_invoices)}")
    print(f"\nFirst 10 invoice codes:")
    for inv in all_invoices[:10]:
        print(f"  - {inv.get('code')} (ID: {inv.get('invoice_id')}, Cust Order: {inv.get('cust_ord_id')})")

print(f"\n{'='*80}")

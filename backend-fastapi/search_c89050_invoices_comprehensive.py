import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()

# Get C89050 order
all_orders = client.get_customer_orders()
c89050 = next((o for o in all_orders if o.get('code') == 'C89050'), None)

if not c89050:
    print("Order C89050 not found!")
    sys.exit(1)

cust_ord_id = c89050.get('cust_ord_id')
print("="*80)
print(f"SEARCHING FOR ALL INVOICES WITH CUSTOMER ORDER: C89050")
print(f"Customer Order ID: {cust_ord_id}")
print("="*80)

# Get all invoices
all_invoices = client.get_invoices()
print(f"Total invoices in system: {len(all_invoices)}\n")

# Find ALL invoices for this customer order
matching_invoices = []
for inv in all_invoices:
    if inv.get('cust_ord_id') == cust_ord_id:
        matching_invoices.append(inv)

print(f"Invoices found for Customer Order {cust_ord_id}: {len(matching_invoices)}\n")

if matching_invoices:
    for idx, inv in enumerate(matching_invoices, 1):
        print(f"\n{'='*80}")
        print(f"INVOICE #{idx}")
        print(f"{'='*80}")
        print(f"Invoice Code: {inv.get('code')}")
        print(f"Invoice ID: {inv.get('invoice_id')}")
        print(f"Status: {inv.get('status_txt')}")
        print(f"Date: {inv.get('date')}")
        print(f"Total Price: {inv.get('total_price')} {inv.get('currency')}")
        print(f"\nProducts:")
        
        total_qty = 0
        for prod in inv.get('products', []):
            item_code = prod.get('item_code')
            qty = prod.get('quantity', 0) or 0
            price = prod.get('price', 0) or 0
            
            if item_code != 'Shipping':
                print(f"  - {item_code}: quantity={qty}, price={price}")
                total_qty += qty
            else:
                print(f"  - {item_code}: quantity={qty}, price={price}")
        
        print(f"\nTotal Quantity (excl. Shipping): {total_qty} units")
    
    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    
    total_invoiced = 0
    for inv in matching_invoices:
        for prod in inv.get('products', []):
            if prod.get('item_code') != 'Shipping':
                qty = prod.get('quantity', 0) or 0
                total_invoiced += qty
    
    shipped = c89050.get('products', [{}])[0].get('shipped', 0) or 0
    
    print(f"Total Invoices: {len(matching_invoices)}")
    print(f"Total Shipped: {shipped} units")
    print(f"Total Invoiced: {total_invoiced} units")
    print(f"Discrepancy: {shipped - total_invoiced} units")
    
    if shipped == total_invoiced:
        print("\n✓ FULLY INVOICED")
    elif shipped > total_invoiced:
        print(f"\n⚠️  UNDER-INVOICED ({shipped - total_invoiced} units missing)")
    else:
        print(f"\n🔴 OVER-INVOICED ({total_invoiced - shipped} units excess)")
else:
    print(f"✗ No invoices found for customer order {cust_ord_id} (C89050)")

print(f"\n{'='*80}")

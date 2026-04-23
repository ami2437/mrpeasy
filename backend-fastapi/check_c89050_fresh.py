import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()

# Get all invoices fresh
all_invoices = client.get_invoices()

print("="*80)
print("SEARCHING FOR ALL INVOICES WITH CUSTOMER ORDER: C89050")
print("="*80)
print(f"Total invoices fetched: {len(all_invoices)}\n")

# Get C89050 order
all_orders = client.get_customer_orders()
c89050 = next((o for o in all_orders if o.get('code') == 'C89050'), None)

if c89050:
    cust_ord_id = c89050.get('cust_ord_id')
    
    # Find all invoices for this customer order
    matching_invoices = []
    for inv in all_invoices:
        if inv.get('cust_ord_id') == cust_ord_id:
            matching_invoices.append(inv)
    
    print(f"Invoices for Order C89050 (Customer Order ID: {cust_ord_id}): {len(matching_invoices)}\n")
    
    total_invoiced = 0
    for idx, inv in enumerate(matching_invoices, 1):
        print(f"\nINVOICE #{idx}")
        print(f"-" * 80)
        print(f"Invoice Code: {inv.get('code')}")
        print(f"Invoice ID: {inv.get('invoice_id')}")
        print(f"Status: {inv.get('status_txt')}")
        print(f"Products:")
        
        for prod in inv.get('products', []):
            item_code = prod.get('item_code')
            qty = prod.get('quantity', 0) or 0
            
            if item_code != 'Shipping':
                print(f"  - {item_code}: {qty} units")
                total_invoiced += qty
            else:
                print(f"  - {item_code}: {qty}")
    
    # Get shipped qty
    shipped = c89050.get('products', [{}])[0].get('shipped', 0) or 0
    
    print(f"\n{'='*80}")
    print("DISCREPANCY")
    print(f"{'='*80}")
    print(f"Shipped: {shipped} units")
    print(f"Invoiced (total): {total_invoiced} units")
    print(f"Discrepancy: {shipped - total_invoiced} units")
    
    if shipped == total_invoiced:
        print("\n✓ FULLY INVOICED")
    elif shipped > total_invoiced:
        print(f"\n⚠️  UNDER-INVOICED ({shipped - total_invoiced} units missing)")
    else:
        print(f"\n🔴 OVER-INVOICED ({total_invoiced - shipped} units excess)")
    
    print(f"\n{'='*80}")

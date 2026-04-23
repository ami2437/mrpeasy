import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()

# Get C89050 order details
all_orders = client.get_customer_orders()
c89050 = next((o for o in all_orders if o.get('code') == 'C89050'), None)

if c89050:
    cust_ord_id = c89050.get('cust_ord_id')
    print("="*80)
    print("C89050 ORDER DETAILS")
    print("="*80)
    print(f"Order Code: {c89050.get('code')}")
    print(f"Customer Order ID: {cust_ord_id}")
    print(f"Invoice Status: {c89050.get('invoice_status')}")
    print()
    print("Order Products:")
    for prod in c89050.get('products', []):
        print(f"  - {prod.get('item_code')}: qty={prod.get('quantity')}, shipped={prod.get('shipped')}")
    print()
    
    # Get ALL invoices and filter by cust_ord_id
    all_invoices = client.get_invoices()
    print(f"Total invoices in system: {len(all_invoices)}")
    
    order_invoices = [inv for inv in all_invoices if inv.get('cust_ord_id') == cust_ord_id]
    
    print(f"\n{'='*80}")
    print(f"ALL INVOICES FOR ORDER {cust_ord_id} (C89050)")
    print(f"{'='*80}")
    print(f"Number of invoices found: {len(order_invoices)}\n")
    
    # Build summed quantities per item
    item_totals = {}
    
    for idx, inv in enumerate(order_invoices, 1):
        print(f"\nInvoice #{idx}")
        print(f"  Invoice ID: {inv.get('invoice_id')}")
        print(f"  Invoice Code: {inv.get('code')}")
        print(f"  Customer Order ID: {inv.get('cust_ord_id')}")
        print(f"  Status: {inv.get('status_txt')}")
        print(f"  Products:")
        for prod in inv.get('products', []):
            item_code = prod.get('item_code')
            qty = prod.get('quantity', 0) or 0
            print(f"    - {item_code}: qty={qty}")
            
            # Sum quantities
            if item_code and item_code != 'Shipping':
                if item_code not in item_totals:
                    item_totals[item_code] = 0
                item_totals[item_code] += qty
    
    print(f"\n{'='*80}")
    print("TOTAL INVOICED QUANTITIES (SUMMED ACROSS ALL INVOICES)")
    print(f"{'='*80}")
    for item_code, total_qty in item_totals.items():
        print(f"  {item_code}: {total_qty} units invoiced")
    
    print(f"\n{'='*80}")
    print("DISCREPANCY CHECK")
    print(f"{'='*80}")
    for prod in c89050.get('products', []):
        item_code = prod.get('item_code')
        shipped = prod.get('shipped', 0) or 0
        invoiced = item_totals.get(item_code, 0)
        discrepancy = shipped - invoiced
        
        print(f"\n{item_code}:")
        print(f"  Shipped: {shipped}")
        print(f"  Invoiced (total): {invoiced}")
        print(f"  Discrepancy: {discrepancy}")
        
        if discrepancy == 0:
            print(f"  ✓ Fully invoiced - NO DISCREPANCY")
        elif discrepancy > 0:
            print(f"  ⚠️  Under-invoiced by {discrepancy} units")
        else:
            print(f"  🔴 Over-invoiced by {abs(discrepancy)} units")
    
    print("="*80)

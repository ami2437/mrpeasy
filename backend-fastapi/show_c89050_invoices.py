import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient
import json

client = MRPeasyAPIClient()

# Get C89050 order
all_orders = client.get_customer_orders()
c89050 = next((o for o in all_orders if o.get('code') == 'C89050'), None)

if c89050:
    cust_ord_id = c89050.get('cust_ord_id')
    print("="*80)
    print(f"ORDER: {c89050.get('code')} (Customer Order ID: {cust_ord_id})")
    print("="*80)
    print(f"Customer: {c89050.get('customer_name')}")
    print(f"Status: {c89050.get('status_txt')}")
    print(f"Invoice Status: {c89050.get('invoice_status')}")
    print()
    print("SHIPPED ITEMS:")
    for prod in c89050.get('products', []):
        print(f"  {prod.get('item_code')}: {prod.get('shipped')} units")
    print()
    
    # Get ALL invoices
    all_invoices = client.get_invoices()
    print(f"Total invoices in system: {len(all_invoices)}")
    
    # Find all invoices for this customer order
    order_invoices = []
    for inv in all_invoices:
        if inv.get('cust_ord_id') == cust_ord_id:
            order_invoices.append(inv)
    
    print(f"\n{'='*80}")
    print(f"INVOICES FOR ORDER {cust_ord_id} (C89050)")
    print(f"{'='*80}")
    print(f"Total invoices found: {len(order_invoices)}\n")
    
    for idx, inv in enumerate(order_invoices, 1):
        print(f"\nINVOICE #{idx}")
        print(f"-" * 80)
        print(f"Invoice ID: {inv.get('invoice_id')}")
        print(f"Invoice Code: {inv.get('code')}")
        print(f"Status: {inv.get('status_txt')}")
        print(f"Date: {inv.get('date')}")
        print(f"Total Price: {inv.get('total_price')} {inv.get('currency')}")
        print(f"Products:")
        
        for prod in inv.get('products', []):
            item_code = prod.get('item_code')
            qty = prod.get('quantity', 0) or 0
            price = prod.get('price', 0) or 0
            print(f"  - {item_code}: quantity={qty}, price={price}")
    
    # Sum all invoiced quantities
    print(f"\n{'='*80}")
    print("TOTAL INVOICED (SUMMED)")
    print(f"{'='*80}")
    
    totals = {}
    for inv in order_invoices:
        for prod in inv.get('products', []):
            item_code = prod.get('item_code')
            qty = prod.get('quantity', 0) or 0
            if item_code and item_code != 'Shipping':
                if item_code not in totals:
                    totals[item_code] = 0
                totals[item_code] += qty
    
    for item_code, qty in totals.items():
        print(f"  {item_code}: {qty} units total")
    
    # Calculate discrepancy
    print(f"\n{'='*80}")
    print("DISCREPANCY ANALYSIS")
    print(f"{'='*80}")
    
    for prod in c89050.get('products', []):
        item_code = prod.get('item_code')
        shipped = prod.get('shipped', 0) or 0
        invoiced = totals.get(item_code, 0)
        discrepancy = shipped - invoiced
        
        print(f"\n{item_code}:")
        print(f"  Shipped: {shipped}")
        print(f"  Total Invoiced: {invoiced}")
        print(f"  Discrepancy: {discrepancy}")
        
        if discrepancy == 0:
            print(f"  Status: ✓ FULLY INVOICED")
        elif discrepancy > 0:
            print(f"  Status: ⚠️  NOT FULLY INVOICED ({discrepancy} units missing)")
        else:
            print(f"  Status: 🔴 OVER-INVOICED ({abs(discrepancy)} units excess)")
    
    print(f"\n{'='*80}")

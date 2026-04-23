#!/usr/bin/env python
"""
Diagnostic Script: Find invoicing discrepancies similar to C89084/Inv-9601564 bug.

Purpose: Identify orders where items were shipped but not included in invoices.
"""

import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')

from app.services.mrpeasy_client import MRPeasyAPIClient
import time

def find_shipping_invoice_mismatches():
    """
    Find orders with items that:
    1. Were shipped (shipped > 0)
    2. But don't appear in the order's invoice(s)
    """
    
    client = MRPeasyAPIClient()
    
    print("\n" + "="*80)
    print("DIAGNOSTIC: Finding Shipping/Invoice Mismatches")
    print("="*80 + "\n")
    
    try:
        # Fetch data
        print("Fetching customer orders... ", end="", flush=True)
        all_orders = client.get_customer_orders()
        print(f"OK ({len(all_orders)} orders)")
        
        print("Fetching invoices... ", end="", flush=True)
        all_invoices = client.get_invoices()
        print(f"OK ({len(all_invoices)} invoices)")
    except Exception as e:
        print(f"ERROR: {e}")
        return
    
    # Build invoices by order ID
    invoices_by_order = {}
    for inv in all_invoices:
        cust_ord_id = inv.get('cust_ord_id')
        if cust_ord_id:
            if cust_ord_id not in invoices_by_order:
                invoices_by_order[cust_ord_id] = []
            invoices_by_order[cust_ord_id].append(inv)
    
    # Find mismatches
    mismatches = []
    
    for order in all_orders:
        cust_ord_id = order.get('cust_ord_id')
        order_code = order.get('code')
        
        # Get invoices for this order
        order_invoices = invoices_by_order.get(cust_ord_id, [])
        
        if not order_invoices:
            continue  # No invoices yet, not a mismatch
        
        # Build set of item codes in order that were shipped
        shipped_items = {}
        for prod in order.get('products', []):
            if prod.get('shipped', 0) > 0:
                item_code = prod.get('item_code')
                quantity = prod.get('quantity', 0)
                shipped = prod.get('shipped', 0)
                if item_code:
                    if item_code not in shipped_items:
                        shipped_items[item_code] = {'qty': 0, 'shipped': 0}
                    shipped_items[item_code]['qty'] += quantity
                    shipped_items[item_code]['shipped'] += shipped
        
        # Build set of item codes in invoices for this order
        invoiced_items = {}
        for inv in order_invoices:
            for prod in inv.get('products', []):
                item_code = prod.get('item_code')
                quantity = prod.get('quantity', 0)
                
                if item_code and str(item_code).lower() != 'shipping':
                    if item_code not in invoiced_items:
                        invoiced_items[item_code] = {'qty': 0, 'invoices': []}
                    invoiced_items[item_code]['qty'] += quantity
                    inv_code = inv.get('code', f"INV-{inv.get('invoice_id')}")
                    if inv_code not in invoiced_items[item_code]['invoices']:
                        invoiced_items[item_code]['invoices'].append(inv_code)
        
        # Find missing items
        missing_items = []
        for item_code, shipped_data in shipped_items.items():
            invoiced_qty = invoiced_items.get(item_code, {}).get('qty', 0)
            shipped_qty = shipped_data['shipped']
            
            if invoiced_qty < shipped_qty:
                missing_qty = shipped_qty - invoiced_qty
                missing_items.append({
                    'item_code': item_code,
                    'shipped': shipped_qty,
                    'invoiced': invoiced_qty,
                    'missing': missing_qty,
                    'in_invoices': invoiced_items.get(item_code, {}).get('invoices', [])
                })
        
        # Over-invoiced items
        over_invoiced = []
        for item_code, invoiced_data in invoiced_items.items():
            shipped_qty = shipped_items.get(item_code, {}).get('shipped', 0)
            invoiced_qty = invoiced_data['qty']
            
            if invoiced_qty > shipped_qty:
                over_qty = invoiced_qty - shipped_qty
                over_invoiced.append({
                    'item_code': item_code,
                    'shipped': shipped_qty,
                    'invoiced': invoiced_qty,
                    'excess': over_qty,
                    'in_invoices': invoiced_data['invoices']
                })
        
        if missing_items or over_invoiced:
            mismatches.append({
                'order_code': order_code,
                'cust_ord_id': cust_ord_id,
                'customer': order.get('customer_name'),
                'invoice_count': len(order_invoices),
                'invoice_codes': [inv.get('code') for inv in order_invoices],
                'missing_items': missing_items,
                'over_invoiced_items': over_invoiced
            })
    
    # Display results
    print(f"\nFound {len(mismatches)} orders with shipping/invoice mismatches:\n")
    
    if not mismatches:
        print("OK: No mismatches found! All shipped items are properly invoiced.")
        return
    
    for i, mismatch in enumerate(mismatches, 1):
        print(f"\n{i}. {mismatch['order_code']} (ID: {mismatch['cust_ord_id']})")
        print(f"   Customer: {mismatch['customer']}")
        print(f"   Invoices: {', '.join(mismatch['invoice_codes'])}")
        
        if mismatch['missing_items']:
            print(f"   \n   < UNDER-INVOICED:")
            for item in mismatch['missing_items']:
                print(f"      - {item['item_code']}: shipped {item['shipped']}, " +
                      f"invoiced {item['invoiced']}, MISSING {item['missing']} " +
                      f"(appears in: {', '.join(item['in_invoices']) if item['in_invoices'] else 'NONE'})")
        
        if mismatch['over_invoiced_items']:
            print(f"   \n   > OVER-INVOICED:")
            for item in mismatch['over_invoiced_items']:
                print(f"      - {item['item_code']}: shipped {item['shipped']}, " +
                      f"invoiced {item['invoiced']}, EXCESS {item['excess']} " +
                      f"(in: {', '.join(item['in_invoices'])})")
    
    print("\n" + "="*80)
    print(f"\nSUMMARY:")
    print(f"  Total orders with mismatches: {len(mismatches)}")
    
    total_under = sum(len(m['missing_items']) for m in mismatches)
    total_over = sum(len(m['over_invoiced_items']) for m in mismatches)
    
    print(f"  Total under-invoiced items: {total_under}")
    print(f"  Total over-invoiced items: {total_over}")
    
    print("\n" + "="*80 + "\n")

if __name__ == "__main__":
    find_shipping_invoice_mismatches()

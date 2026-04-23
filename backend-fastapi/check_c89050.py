import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()

# Get C89050 order details
all_orders = client.get_customer_orders()
c89050 = next((o for o in all_orders if o.get('code') == 'C89050'), None)

if c89050:
    print("="*80)
    print("C89050 ORDER DETAILS")
    print("="*80)
    print(f"Order Code: {c89050.get('code')}")
    print(f"Customer Order ID: {c89050.get('cust_ord_id')}")
    print(f"Customer: {c89050.get('customer_name')}")
    print(f"Invoice Status: {c89050.get('invoice_status')}")
    print(f"Status Text: {c89050.get('status_txt')}")
    print()
    print("Products:")
    for prod in c89050.get('products', []):
        print(f"  - {prod.get('item_code')}: qty={prod.get('quantity')}, shipped={prod.get('shipped')}")
    print()
    
    # Get all invoices for this order
    all_invoices = client.get_invoices()
    order_invoices = [inv for inv in all_invoices if inv.get('cust_ord_id') == c89050.get('cust_ord_id')]
    
    print(f"Invoices found: {len(order_invoices)}")
    for inv in order_invoices:
        print(f"\nInvoice ID: {inv.get('invoice_id')}")
        print(f"Invoice Code: {inv.get('code')}")
        print(f"Products:")
        for prod in inv.get('products', []):
            print(f"  - {prod.get('item_code')}: qty={prod.get('quantity')}")
    print("="*80)

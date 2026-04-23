import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()
all_invoices = client.get_invoices()
all_orders = client.get_customer_orders()

# Find the invoice
for inv in all_invoices:
    if inv.get('code') == 'Inv-9601564':
        print('='*80)
        print('INVOICE: Inv-9601564')
        print('='*80)
        print(f'Invoice Code: {inv.get("code")}')
        print(f'Invoice ID: {inv.get("invoice_id")}')
        print(f'Cust Order ID: {inv.get("cust_ord_id")}')
        print(f'Status: {inv.get("status_txt")}')
        print(f'\nProducts ({len(inv.get("products", []))} items):')
        for i, prod in enumerate(inv.get('products', []), 1):
            print(f'  {i}. {prod.get("item_code")}: qty={prod.get("quantity")}, price={prod.get("price")}')
        
        # Now find and show the order
        print('\n' + '='*80)
        print('CORRESPONDING ORDER')
        print('='*80)
        
        for order in all_orders:
            if order.get('cust_ord_id') == inv.get('cust_ord_id'):
                print(f'Order Code: {order.get("code")}')
                print(f'Customer Order ID: {order.get("cust_ord_id")}')
                print(f'Customer: {order.get("customer_name")}')
                print(f'Status: {order.get("status_txt")}')
                print(f'Invoice Status: {order.get("invoice_status")}')
                
                # Show all products in the order with delivery dates
                print(f'\nOrder Products ({len(order.get("products", []))} items):')
                for i, prod in enumerate(order.get('products', []), 1):
                    print(f'\n  {i}. Item: {prod.get("item_code")}')
                    print(f'     Quantity: {prod.get("quantity")}')
                    print(f'     Shipped: {prod.get("shipped", 0)}')
                    print(f'     Delivery Date: {prod.get("delivery_date")}')
                
                break
        break
else:
    print('Invoice not found')

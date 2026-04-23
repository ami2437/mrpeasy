import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.models import Invoice, CustomerOrder
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Create database session
engine = create_engine('sqlite:///app/mrpeasy.db')
Session = sessionmaker(bind=engine)
session = Session()

print('='*80)
print('BUG ANALYSIS: Inv-9601564 & Order C89084')
print('='*80)

# Find the invoice
invoice = session.query(Invoice).filter(Invoice.code == 'Inv-9601564').first()

# Find the order
order = session.query(CustomerOrder).filter(CustomerOrder.code == 'C89084').first()

if invoice and order:
    print('\nINVOICE CONTENTS (Inv-9601564):')
    print('-' * 80)
    print(f'Invoice ID: {invoice.invoice_id}')
    print(f'Invoice Code: {invoice.code}')
    
    if invoice.products:
        invoice_items = invoice.products
        print(f'Total products stored: {len(invoice_items)}')
        for i, prod in enumerate(invoice_items, 1):
            item_code = prod.get('item_code')
            qty = prod.get('quantity')
            print(f'  {i}. {item_code}: qty={qty}')
    else:
        print('No products data in invoice')
    
    print('\n\nORDER LINE ITEMS (C89084):')
    print('-' * 80)
    print(f'Order ID: {order.cust_ord_id}')
    print(f'Order Code: {order.code}')
    
    if order.products:
        order_products = order.products
        print(f'Total line items stored: {len(order_products)}')
        
        # Group by item code
        items_by_code = {}
        for prod in order_products:
            code = prod.get('item_code')
            if code not in items_by_code:
                items_by_code[code] = []
            items_by_code[code].append(prod)
        
        # Show grouped items
        for item_code in sorted(items_by_code.keys()):
            lines = items_by_code[item_code]
            total_qty = sum(p.get('quantity', 0) for p in lines)
            total_shipped = sum(p.get('shipped', 0) for p in lines)
            print(f'\n  Item: {item_code}')
            print(f'    Total quantity: {total_qty}')
            print(f'    Total shipped: {total_shipped}')
            for j, prod in enumerate(lines, 1):
                print(f'      Line {j}: qty={prod.get("quantity")}, shipped={prod.get("shipped")}')
    else:
        print('No products data in order')
    
    print('\n\nTHE ISSUE:')
    print('-' * 80)
    
    if invoice.products and order.products:
        # Get shipped items from order
        shipped_items = {}
        for prod in order.products:
            code = prod.get('item_code')
            shipped = prod.get('shipped', 0)
            if shipped > 0:
                if code not in shipped_items:
                    shipped_items[code] = 0
                shipped_items[code] += shipped
        
        print(f'Items shipped (and should be invoiced): {shipped_items}')
        
        # Get items in invoice
        invoice_items = {}
        for prod in invoice.products:
            code = prod.get('item_code')
            if code != 'Shipping':
                qty = prod.get('quantity', 0)
                invoice_items[code] = qty
        
        print(f'Items in invoice: {invoice_items}')
        
        # Find missing items
        for code, qty in shipped_items.items():
            if code not in invoice_items:
                print(f'\n✗ MISSING: {code} (shipped {qty} units)')
                for prod in order.products:
                    if prod.get('item_code') == code and prod.get('shipped', 0) > 0:
                        print(f'    - Order line item {code} was SHIPPED but missing from INVOICE')
else:
    if not invoice:
        print('Invoice Inv-9601564 not found in local database')
    if not order:
        print('Order C89084 not found in local database')

session.close()

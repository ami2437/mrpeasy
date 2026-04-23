import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()
all_invoices = client.get_invoices()
all_orders = client.get_customer_orders()

print('='*80)
print('BUG ANALYSIS: Inv-9601564 & Order C89084')
print('='*80)

# Find the invoice and order
invoice = None
order = None

for inv in all_invoices:
    if inv.get('code') == 'Inv-9601564':
        invoice = inv
        break

for o in all_orders:
    if o.get('code') == 'C89084':
        order = o
        break

if invoice and order:
    print('\nINVOICE CONTENTS (Inv-9601564):')
    print('-' * 80)
    invoice_items = invoice.get('products', [])
    print(f'Total products in invoice: {len(invoice_items)}')
    for i, prod in enumerate(invoice_items, 1):
        item_code = prod.get('item_code')
        qty = prod.get('quantity')
        print(f'  {i}. {item_code}: qty={qty}')
    
    print('\n\nORDER LINE ITEMS (C89084):')
    print('-' * 80)
    order_products = order.get('products', [])
    print(f'Total line items in order: {len(order_products)}')
    
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
        print(f'    Total quantity across all lines: {total_qty}')
        print(f'    Total shipped: {total_shipped}')
        for j, prod in enumerate(lines, 1):
            delivery_date = prod.get('delivery_date')
            delivery_str = f'<future>' if delivery_date > 1776000000 else '<past>'
            print(f'      Line {j}: qty={prod.get("quantity")}, shipped={prod.get("shipped")}, delivery={delivery_str}')
    
    print('\n\nTHE BUG:')
    print('-' * 80)
    
    # Check which items were shipped
    shipped_items = set()
    for prod in order_products:
        if prod.get('shipped', 0) > 0:
            shipped_items.add(prod.get('item_code'))
    
    print(f'Items that were SHIPPED (should be in invoice): {sorted(shipped_items)}')
    
    # Check which items are in invoice
    invoice_items_set = set()
    for prod in invoice_items:
        if prod.get('item_code') != 'Shipping':
            invoice_items_set.add(prod.get('item_code'))
    
    print(f'Items actually in INVOICE: {sorted(invoice_items_set)}')
    
    missing_from_invoice = shipped_items - invoice_items_set
    if missing_from_invoice:
        print(f'\n✗ MISSING FROM INVOICE: {missing_from_invoice}')
        for item in missing_from_invoice:
            for prod in order_products:
                if prod.get('item_code') == item and prod.get('shipped', 0) > 0:
                    print(f'    - {item}: shipped {prod.get("shipped")} units but NOT in invoice!')
    
    print('\n' + '='*80)

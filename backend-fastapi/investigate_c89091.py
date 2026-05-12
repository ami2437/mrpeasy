import sqlite3
import json

conn = sqlite3.connect('mrpeasy.db')
cursor = conn.cursor()

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = cursor.fetchall()
print('Tables in database:')
for t in tables:
    print(f'  {t[0]}')

print('\n' + '='*80)
print('ORDER C89091 INVESTIGATION')
print('='*80)

# Get order
cursor.execute("SELECT * FROM customer_orders WHERE code = 'C89091'")
order = cursor.fetchone()

if order:
    print('\nORDER FOUND:')
    cursor.execute("PRAGMA table_info(customer_orders)")
    columns = cursor.fetchall()
    for i, col in enumerate(columns):
        if i < len(order):
            print(f"  {col[1]}: {order[i]}")
    
    # Get products
    order_id = order[0]
    cursor.execute("SELECT * FROM customer_order_products WHERE customer_order_id = ?", (order_id,))
    products = cursor.fetchall()
    
    if products:
        print(f'\nPRODUCTS ({len(products)}):')
        cursor.execute("PRAGMA table_info(customer_order_products)")
        columns = [col[1] for col in cursor.fetchall()]
        
        for prod in products:
            print(f'\n  Product ID: {prod[0]}')
            for i, col in enumerate(columns):
                if i < len(prod):
                    print(f'    {col}: {prod[i]}')
    
    # Get shipments
    order_code = order[2]  # code
    cursor.execute("SELECT DISTINCT shipment_code FROM shipments WHERE customer_order_code = ?", (order_code,))
    shipment_codes = cursor.fetchall()
    
    print(f'\nSHIPMENTS ({len(shipment_codes)}):')
    for sc in shipment_codes:
        shipment_code = sc[0]
        print(f'\n  Shipment: {shipment_code}')
        
        cursor.execute("SELECT * FROM shipment_items WHERE shipment_code = ? ORDER BY product_code", (shipment_code,))
        items = cursor.fetchall()
        
        if items:
            cursor.execute("PRAGMA table_info(shipment_items)")
            columns = [col[1] for col in cursor.fetchall()]
            
            for item in items:
                print(f'    Item ID: {item[0]}')
                for i, col in enumerate(columns):
                    if i < len(item):
                        print(f'      {col}: {item[i]}')
else:
    print('Order C89091 not found')

conn.close()

import json
from app.services.mrpeasy_client import mrpeasy_client

# Simulate what frontend sends for SH215601
shipment_code = 'SH215601'
shipments = mrpeasy_client.get_shipments()
sh = next((s for s in shipments if s.get('code') == shipment_code), None)

print("Simulating frontend label generation for SH215601\n")

# Get shipment details first
customer_order_id = sh.get('customer_order_id')
customer_order = mrpeasy_client.get_customer_order(customer_order_id)

# Enrich products with order_line (as the API would do)
shipment_products = sh.get('products', [])

# This is what the backend endpoint does
order_line_needs = {}
for order_product in customer_order.get('products', []):
    ord_num = order_product.get('ord')
    item_code = order_product.get('item_code')
    qty = order_product.get('quantity', 0)
    
    lots = set()
    for source in order_product.get('source', []):
        lot_code = source.get('lot_code')
        if lot_code:
            lots.add(lot_code)
    
    key = (item_code, ord_num)
    order_line_needs[key] = {
        'lots': lots,
        'remaining': qty
    }

# Assign order_line to each product
for product in shipment_products:
    lot_code = product.get('lot_code')
    qty_booked = product.get('quantity_booked', 0)
    item_code = product.get('item_code')
    
    assigned = False
    for (need_item, need_ord), line_info in order_line_needs.items():
        if need_item == item_code and lot_code in line_info['lots'] and line_info['remaining'] > 0:
            product['order_line'] = need_ord
            line_info['remaining'] -= qty_booked
            assigned = True
            break

print("Products with order_line assigned:")
for i, p in enumerate(shipment_products):
    print(f"  Product {i}: {p['item_code']} qty={p['quantity_booked']} lot={p['lot_code']} order_line={p.get('order_line', 'NOT SET')}")

# Now simulate what the frontend would send to generate endpoint
print("\n\nSimulating frontend POST data to /api/labels/generate/{shipment_code}:")
product_configs = {}
for i, product in enumerate(shipment_products):
    item_code = product['item_code']
    product_key = f"{item_code}-{i}"
    product_configs[product_key] = {
        'item_code': item_code,
        'order_line': product.get('order_line', '1'),
        'pack_size': 1,
        'label_mode': 'individual'
    }

print(json.dumps(product_configs, indent=2))

# Now simulate the combining logic in the backend
print("\n\nSimulating backend combining logic:")
combined_groups = {}
for product_key, config in product_configs.items():
    item_code = config.get('item_code')
    order_line = config.get('order_line', '1')
    pack_size = config.get('pack_size', 1)
    
    try:
        prod_index = int(product_key.split('-')[-1])
        if prod_index >= len(shipment_products):
            print(f"  Skipping {product_key}: index out of range")
            continue
        product = shipment_products[prod_index]
    except (ValueError, IndexError):
        print(f"  Skipping {product_key}: invalid index")
        continue
    
    group_key = (item_code, order_line)
    print(f"  Processing {product_key}: item_code={item_code}, order_line={order_line}, group_key={group_key}")
    
    if group_key not in combined_groups:
        combined_groups[group_key] = {
            'item_code': item_code,
            'item_title': product.get('item_title'),
            'order_line': order_line,
            'pack_size': pack_size,
            'lot_codes': [],
            'total_quantity': 0,
            'products': []
        }
        print(f"    → Created new group")
    
    combined_groups[group_key]['lot_codes'].append(product.get('lot_code'))
    combined_groups[group_key]['total_quantity'] += product.get('quantity_booked', 0)
    combined_groups[group_key]['products'].append(product)
    print(f"    → Added to group, new total_quantity={combined_groups[group_key]['total_quantity']}")

print("\n\nFinal combined groups:")
for group_key, item_data in combined_groups.items():
    print(f"  Group {group_key}:")
    print(f"    - item_code: {item_data['item_code']}")
    print(f"    - order_line: {item_data['order_line']}")
    print(f"    - lot_codes: {item_data['lot_codes']}")
    print(f"    - total_quantity: {item_data['total_quantity']}")

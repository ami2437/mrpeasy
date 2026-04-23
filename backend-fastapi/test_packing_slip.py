"""
Test the packing slip data format
"""
import json
import sqlite3

db_path = 'mrpeasy.db'
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Get all shipment boxes for SH215601
cursor.execute('SELECT * FROM shipment_boxes WHERE shipment_code = ? ORDER BY item_code, order_line, box_number', 
               ('SH215601',))
boxes = cursor.fetchall()

# Group by item_code + order_line
grouped_items = {}

for box in boxes:
    group_key = f"{box['item_code']}:{box['order_line']}"
    
    if group_key not in grouped_items:
        grouped_items[group_key] = {
            'shipment_code': box['shipment_code'],
            'item_code': box['item_code'],
            'item_title': box['item_title'],
            'order_line': box['order_line'],
            'po_number': box['po_number'],
            'finalized_at': box['finalized_at'],
            'boxes_by_quantity': {},
            'total_qty_shipped': 0,
            'pallet_number': box['pallet_number']
        }
    
    # Track boxes by quantity
    qty = box['quantity_in_box']
    if qty not in grouped_items[group_key]['boxes_by_quantity']:
        grouped_items[group_key]['boxes_by_quantity'][qty] = 0
    grouped_items[group_key]['boxes_by_quantity'][qty] += 1
    
    # Add to total
    grouped_items[group_key]['total_qty_shipped'] += qty

# Format output
print("=" * 80)
print("PACKING SLIP FOR SHIPMENT SH215601")
print("=" * 80)

for group_key, item_data in grouped_items.items():
    # Create box breakdown string
    box_breakdown_parts = []
    for qty in sorted(item_data['boxes_by_quantity'].keys(), reverse=True):
        count = item_data['boxes_by_quantity'][qty]
        box_breakdown_parts.append(f"{count} box of {qty}")
    box_breakdown = ', '.join(box_breakdown_parts)
    
    print(f"\nShipment: {item_data['shipment_code']}")
    print(f"  Item Code: {item_data['item_code']}")
    print(f"  Description: {item_data['item_title']}")
    print(f"  Order Line: {item_data['order_line']}")
    print(f"  PO #: {item_data['po_number']}")
    print(f"  Finalized: {item_data['finalized_at']}")
    print(f"  Qty Shipped: {item_data['total_qty_shipped']}")
    print(f"  Box Breakdown: {box_breakdown}")
    if item_data['pallet_number']:
        print(f"  Pallet #: {item_data['pallet_number']}")

print("\n" + "=" * 80)

conn.close()

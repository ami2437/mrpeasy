"""
Test script demonstrating the new packing slip workflow
"""
import json
import requests
from datetime import datetime

API_BASE = 'http://localhost:8000'

def test_workflow():
    """Test the complete finalize → packing slip workflow"""
    
    # 1. Get a ready shipment
    print("1. Getting ready shipments...")
    response = requests.get(f'{API_BASE}/api/labels/shipments/ready')
    shipments = response.json()['shipments']
    
    if not shipments:
        print("No ready shipments found")
        return
    
    shipment_code = shipments[0]['code']
    print(f"   Found shipment: {shipment_code}")
    
    # 2. Get shipment details
    print(f"\n2. Getting shipment details for {shipment_code}...")
    response = requests.get(f'{API_BASE}/api/labels/shipments/{shipment_code}')
    shipment = response.json()['shipment']
    print(f"   Products: {len(shipment['products'])}")
    
    # 3. Build product configs for finalization
    print(f"\n3. Building product configs...")
    product_configs = {}
    for prod_idx, product in enumerate(shipment['products']):
        product_key = f"{product['item_code']}-{prod_idx}"
        product_configs[product_key] = {
            'item_code': product['item_code'],
            'order_line': product.get('order_line', '1'),
            'pack_size': 25  # Example pack size
        }
    
    print(f"   Created configs for {len(product_configs)} products")
    
    # 4. Finalize shipment (save to DB)
    print(f"\n4. Finalizing shipment with pallet...")
    response = requests.post(
        f'{API_BASE}/api/labels/finalize/{shipment_code}',
        json={
            'pallet_number': 'PALLET-001',
            'product_configs': product_configs
        }
    )
    finalize_result = response.json()
    
    if finalize_result['success']:
        print(f"   ✓ Finalized! Saved {finalize_result['total_boxes_saved']} boxes")
        print(f"   Pallet: {finalize_result['pallet_number']}")
    else:
        print(f"   ✗ Failed: {finalize_result}")
        return
    
    # 5. Get packing slip data
    print(f"\n5. Getting packing slip data...")
    response = requests.get(f'{API_BASE}/api/packing-slip/{shipment_code}')
    packing_data = response.json()
    
    if packing_data['success']:
        print(f"   ✓ Retrieved packing slip data")
        print(f"   Total items: {packing_data['total_items']}")
        print(f"   Total boxes: {packing_data['total_boxes']}")
        
        print("\n   Items Summary:")
        for item in packing_data['items_summary']:
            print(f"     - {item['item_code']}: {item['total_quantity']} qty in {item['total_boxes']} boxes")
    else:
        print(f"   ✗ Failed: {packing_data}")
    
    # 6. Show all boxes
    print(f"\n6. All Boxes Detail:")
    for box in packing_data['all_boxes'][:5]:  # Show first 5
        print(f"   Box {box['box_number']}: {box['quantity_in_box']} qty | {box['item_code']} | Order Line {box['order_line']}")
    if len(packing_data['all_boxes']) > 5:
        print(f"   ... and {len(packing_data['all_boxes']) - 5} more boxes")

if __name__ == '__main__':
    test_workflow()

from app.services.mrpeasy_client import mrpeasy_client
import json
from app.main import app
from fastapi.testclient import TestClient

shipments = mrpeasy_client.get_shipments()
sh = next((s for s in shipments if s.get('code') == 'SH215601'), None)

if sh:
    print('Shipment:', sh.get('code'))
    print('Customer Order ID:', sh.get('customer_order_id'))
    print('\nProducts (Raw):')
    for p in sh.get('products', []):
        print(f"  - {p.get('item_code')}: qty={p.get('quantity_booked')}, lot={p.get('lot_code')}, order_line={p.get('order_line', 'NOT SET')}")
    
    # Now test the API endpoint
    print('\n\nTesting API endpoint /api/labels/shipments/SH215601:')
    client = TestClient(app)
    response = client.get('/api/labels/shipments/SH215601')
    if response.status_code == 200:
        data = response.json()
        shipment_data = data.get('shipment', {})
        print('\nProducts (From API):')
        for p in shipment_data.get('products', []):
            print(f"  - {p.get('item_code')}: qty={p.get('quantity_booked')}, lot={p.get('lot_code')}, order_line={p.get('order_line', 'NOT SET')}")
    else:
        print(f'Error: {response.status_code}')
    
    # Show customer order for reference
    cust_order_id = sh.get('customer_order_id')
    if cust_order_id:
        cust_order = mrpeasy_client.get_customer_order(cust_order_id)
        print('\n\nCustomer Order Details:')
        for product in cust_order.get('products', []):
            print(f"\nLine {product.get('ord')}: {product.get('item_code')} qty={product.get('quantity')}")
            print("  Source lots:")
            for source in product.get('source', []):
                print(f"    - {source.get('lot_code')}")
else:
    print('Shipment SH215601 not found')

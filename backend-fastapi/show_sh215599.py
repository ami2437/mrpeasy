from app.services.mrpeasy_client import mrpeasy_client
import json

# Get shipment SH215599
shipments = mrpeasy_client.get_shipments()
sh = next((s for s in shipments if s.get('code') == 'SH215599'), None)

if sh:
    print("=" * 80)
    print("SHIPMENT SH215599 DATA")
    print("=" * 80)
    print(json.dumps(sh, indent=2))
    
    # Get customer order
    cust_order_id = sh.get('customer_order_id')
    if cust_order_id:
        cust_order = mrpeasy_client.get_customer_order(cust_order_id)
        print("\n" + "=" * 80)
        print(f"CUSTOMER ORDER DATA (ID: {cust_order_id})")
        print("=" * 80)
        print(json.dumps(cust_order, indent=2))
else:
    print("Shipment SH215599 not found")

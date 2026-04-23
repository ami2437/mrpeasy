"""
Script to display the full shipment data structure from MRPeasy API
"""
import json
from app.services.mrpeasy_client import mrpeasy_client

# Get all shipments first
print("Fetching all shipments...")
shipments = mrpeasy_client.get_shipments()

print(f"\nTotal shipments found: {len(shipments)}")

# Show first few shipments with their status
print("\nFirst 5 shipments:")
for i, s in enumerate(shipments[:5]):
    print(f"  {i+1}. {s.get('code')} - Status: {s.get('status')} - ID: {s.get('id')}")

# Filter for ready shipments
ready_shipments = [s for s in shipments if s.get('status') == 'Ready for shipment']

if ready_shipments:
    print(f"\nFound {len(ready_shipments)} ready shipment(s)\n")
    
    # Get detailed data for the first ready shipment
    first_shipment = ready_shipments[0]
    shipment_id = first_shipment.get('id')
    
    print(f"Fetching detailed data for shipment ID: {shipment_id}")
    print(f"Shipment Code: {first_shipment.get('code')}\n")
    print("="*80)
    
    # Get full shipment details
    shipment_details = mrpeasy_client.get_shipment(shipment_id)
    
    print("\nFULL SHIPMENT DATA STRUCTURE:")
    print("="*80)
    print(json.dumps(shipment_details, indent=2))
    print("="*80)
    
    # Show key fields
    print("\n\nKEY FIELDS AVAILABLE:")
    print("="*80)
    for key in sorted(shipment_details.keys()):
        value = shipment_details[key]
        value_type = type(value).__name__
        if isinstance(value, (list, dict)):
            print(f"  {key}: <{value_type} with {len(value)} items>")
        else:
            print(f"  {key}: {value} <{value_type}>")
    
    # Show products structure if available
    if 'products' in shipment_details and shipment_details['products']:
        print("\n\nPRODUCTS STRUCTURE:")
        print("="*80)
        print(json.dumps(shipment_details['products'][0], indent=2))
        print("\n\nPRODUCT FIELDS:")
        for key in sorted(shipment_details['products'][0].keys()):
            print(f"  {key}")
    
else:
    print("\nNo shipments with 'Ready for shipment' status found.")
    print("\nShowing first shipment data structure instead:")
    if shipments and len(shipments) > 0:
        first_shipment = shipments[0]
        print(f"\nShipment: {first_shipment.get('code')}")
        print(f"Status: {first_shipment.get('status')}")
        shipment_id = first_shipment.get('id')
        if shipment_id:
            shipment_details = mrpeasy_client.get_shipment(shipment_id)
            print("\nFULL SHIPMENT DATA:")
            print("="*80)
            print(json.dumps(shipment_details, indent=2))
        else:
            print("\nNo shipment ID found. Showing basic data:")
            print(json.dumps(first_shipment, indent=2))
    else:
        print("No shipments found at all.")

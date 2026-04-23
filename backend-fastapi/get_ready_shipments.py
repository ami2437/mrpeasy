"""
Get shipments with 'Ready for shipment' status
"""
import sys
from pathlib import Path

# Add the parent directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.mrpeasy_client import mrpeasy_client
import json

def get_ready_shipments():
    """Get all shipments and filter for 'Ready for shipment' status"""
    try:
        print("🔄 Fetching all shipments from MRPeasy API...")
        shipments = mrpeasy_client.get_shipments()
        
        if not shipments:
            print("⚠️  No shipments found")
            return
        
        print(f"✅ Retrieved {len(shipments)} total shipments\n")
        
        # Filter for Ready for shipment status
        ready_shipments = []
        for shipment in shipments:
            status_txt = shipment.get('status_txt', '').lower()
            if 'ready' in status_txt and 'shipment' in status_txt:
                ready_shipments.append(shipment)
        
        print(f"📦 Found {len(ready_shipments)} shipments with 'Ready for shipment' status:\n")
        print("="*100)
        
        for shipment in ready_shipments:
            print(f"\n📦 Shipment Code: {shipment.get('code')}")
            print(f"   Status: {shipment.get('status_txt')} (ID: {shipment.get('status')})")
            print(f"   Customer Order: {shipment.get('customer_order_code')}")
            print(f"   Delivery Date: {shipment.get('delivery_date')}")
            print(f"   Tracking: {shipment.get('tracking_number') or 'Not set'}")
            print(f"   Products: {len(shipment.get('products', []))} items")
            
            # Show product details
            for product in shipment.get('products', []):
                print(f"      - {product.get('item_code')}: {product.get('quantity_picked')} pcs - {product.get('item_title')[:60]}")
            
            print("-"*100)
        
        print(f"\n📊 Summary: {len(ready_shipments)} shipments ready for shipment")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    get_ready_shipments()

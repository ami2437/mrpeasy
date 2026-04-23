"""
Get detailed item quantities for shipment SH215598
"""
import sys
from pathlib import Path

# Add the parent directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.mrpeasy_client import mrpeasy_client
import json

def get_shipment_details():
    """Get detailed item quantities for ready shipments"""
    try:
        print("🔄 Fetching shipments from MRPeasy API...")
        shipments = mrpeasy_client.get_shipments()
        
        if not shipments:
            print("⚠️  No shipments found")
            return
        
        # Find shipment SH215598
        target_shipment = None
        for shipment in shipments:
            if shipment.get('code') == 'SH215598':
                target_shipment = shipment
                break
        
        if not target_shipment:
            print("❌ Shipment SH215598 not found")
            return
        
        print(f"📦 Shipment: {target_shipment.get('code')}")
        print(f"   Status: {target_shipment.get('status_txt')} (ID: {target_shipment.get('status')})")
        print(f"   Customer Order: {target_shipment.get('customer_order_code')}")
        print(f"   Delivery Date: {target_shipment.get('delivery_date')}")
        print("\n" + "="*100)
        print(f"\n📋 ITEMS TO SHIP ({len(target_shipment.get('products', []))} items):\n")
        
        total_qty = 0
        for idx, product in enumerate(target_shipment.get('products', []), 1):
            qty = product.get('quantity_picked', 0)
            total_qty += qty
            
            print(f"\n{idx}. Item Code: {product.get('item_code')}")
            print(f"   Item Title: {product.get('item_title')}")
            print(f"   Quantity to Ship: {qty} pcs")
            print(f"   Lot Code: {product.get('lot_code')}")
            print(f"   Location: {product.get('site')} - {product.get('location')}")
            print(f"   Product ID: {product.get('product_id')}")
            print("-"*100)
        
        print(f"\n📊 SUMMARY:")
        print(f"   Total Items: {len(target_shipment.get('products', []))}")
        print(f"   Total Quantity: {total_qty} pieces")
        
        # Show full JSON for inspection
        print(f"\n📄 Full Shipment Data:")
        print(json.dumps(target_shipment, indent=2))
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    get_shipment_details()

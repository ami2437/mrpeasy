"""
Labeling System for Shipments
Split items by pack size and generate labels
"""
import sys
from pathlib import Path

# Add the parent directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.mrpeasy_client import mrpeasy_client
from app.config.database import SessionLocal
import json
from typing import List, Dict
from datetime import datetime

def calculate_box_splits(quantity: int, pack_size: int = 1) -> List[int]:
    """
    Split quantity by pack size into boxes.
    Returns list of quantities per box.
    
    Example: quantity=50, pack_size=6 -> [6, 6, 6, 6, 6, 6, 6, 6, 2]
    """
    if pack_size <= 0:
        pack_size = 1
    
    full_boxes = quantity // pack_size
    remaining = quantity % pack_size
    
    boxes = [pack_size] * full_boxes
    if remaining > 0:
        boxes.append(remaining)
    
    return boxes


def generate_labels_for_shipment(shipment: Dict) -> Dict:
    """
    Generate label information for a shipment.
    Each item is split by pack size.
    """
    shipment_code = shipment.get('code', 'UNKNOWN')
    shipment_id = shipment.get('shipment_id')
    customer_order_code = shipment.get('customer_order_code', 'N/A')
    delivery_date = shipment.get('delivery_date', 'Not set')
    
    # Parse shipping address
    shipping_address = shipment.get('shipping_address', '{}')
    try:
        if isinstance(shipping_address, str):
            import json
            addr = json.loads(shipping_address)
        else:
            addr = shipping_address
    except:
        addr = {}
    
    company = addr.get('company', 'Unknown')
    street1 = addr.get('street_line_1', '')
    street2 = addr.get('street_line_2', '')
    city = addr.get('city', '')
    state = addr.get('state', '')
    postal_code = addr.get('postal_code', '')
    
    labels = []
    label_number = 1
    
    products = shipment.get('products', [])
    
    for product in products:
        item_code = product.get('item_code', 'UNKNOWN')
        item_title = product.get('item_title', '')
        quantity_picked = product.get('quantity_picked', 0)
        lot_code = product.get('lot_code', '')
        
        # Default pack size is 1 if not specified
        # In a real system, this would come from a product master table
        # For now, we'll use a default pack size of 1
        pack_size = 1  # TODO: Get from product master data
        
        # Calculate box splits
        boxes = calculate_box_splits(quantity_picked, pack_size)
        
        # Generate a label for each box
        for box_idx, box_qty in enumerate(boxes, start=1):
            label = {
                'label_number': label_number,
                'shipment_code': shipment_code,
                'customer_order': customer_order_code,
                'item_code': item_code,
                'item_title': item_title,
                'lot_code': lot_code,
                'box_number': f"{box_idx}/{len(boxes)}",
                'quantity_in_box': box_qty,
                'total_quantity': quantity_picked,
                'pack_size': pack_size,
                'delivery_date': delivery_date,
                'ship_to_company': company,
                'ship_to_address': f"{street1}, {street2}".strip(', '),
                'ship_to_city_state_zip': f"{city}, {state} {postal_code}".strip(),
            }
            labels.append(label)
            label_number += 1
    
    return {
        'shipment_code': shipment_code,
        'shipment_id': shipment_id,
        'customer_order': customer_order_code,
        'status': shipment.get('status_txt'),
        'total_labels': len(labels),
        'labels': labels
    }


def get_non_shipped_shipments():
    """
    Get all shipments that are NOT Shipped or Cancelled.
    Status codes: 10=New, 20=Shipped, 30=Delivered, 40=Cancelled
    """
    try:
        print("🔄 Fetching shipments from MRPeasy...")
        shipments = mrpeasy_client.get_shipments()
        
        if not shipments:
            print("⚠️  No shipments found")
            return
        
        print(f"✅ Retrieved {len(shipments)} total shipments")
        
        # Filter for non-shipped and non-cancelled
        # Status 20 = Shipped, 40 = Cancelled
        non_shipped = [s for s in shipments if s.get('status') not in [20, 40]]
        
        print(f"\n📊 Filtering Results:")
        print(f"   Total Shipments: {len(shipments)}")
        print(f"   Non-Shipped/Non-Cancelled: {len(non_shipped)}")
        print(f"   Shipped: {sum(1 for s in shipments if s.get('status') == 20)}")
        print(f"   Cancelled: {sum(1 for s in shipments if s.get('status') == 40)}")
        
        print("\n" + "="*100)
        print("📦 NON-SHIPPED SHIPMENTS - READY FOR LABELING")
        print("="*100)
        
        for shipment in non_shipped:
            print(f"\n🏷️  Shipment: {shipment.get('code')}")
            print(f"   Status: {shipment.get('status_txt')} (ID: {shipment.get('status')})")
            print(f"   Customer Order: {shipment.get('customer_order_code')}")
            print(f"   Delivery Date: {datetime.fromtimestamp(int(shipment.get('delivery_date', 0))).strftime('%Y-%m-%d') if shipment.get('delivery_date') else 'Not set'}")
            print(f"   Products: {len(shipment.get('products', []))}")
            
            # Generate labels for this shipment
            label_data = generate_labels_for_shipment(shipment)
            
            print(f"\n   📋 LABELS GENERATED: {label_data['total_labels']} labels")
            print("   " + "-"*96)
            
            for label in label_data['labels']:
                print(f"   Label #{label['label_number']:03d} | Item: {label['item_code']} | Box: {label['box_number']} | Qty: {label['quantity_in_box']} | Lot: {label['lot_code']}")
            
            print("-"*100)
        
        # Summary
        total_labels = sum(generate_labels_for_shipment(s)['total_labels'] for s in non_shipped)
        print(f"\n📊 LABELING SUMMARY:")
        print(f"   Shipments to Process: {len(non_shipped)}")
        print(f"   Total Labels to Print: {total_labels}")
        
        # Demo: Show detailed label example
        if non_shipped:
            print(f"\n📄 SAMPLE LABEL (First shipment, first label):")
            print("="*100)
            first_shipment_labels = generate_labels_for_shipment(non_shipped[0])
            if first_shipment_labels['labels']:
                label = first_shipment_labels['labels'][0]
                print(f"""
┌────────────────────────────────────────────────────────────────┐
│  SHIPPING LABEL #{label['label_number']:03d}                                          │
├────────────────────────────────────────────────────────────────┤
│  SHIPMENT:  {label['shipment_code']:<48} │
│  ORDER:     {label['customer_order']:<48} │
├────────────────────────────────────────────────────────────────┤
│  ITEM:      {label['item_code']:<48} │
│  {label['item_title'][:62]:<62} │
│  LOT:       {label['lot_code']:<48} │
├────────────────────────────────────────────────────────────────┤
│  BOX:       {label['box_number']:<48} │
│  QTY:       {label['quantity_in_box']:<48} │
│  PACK SIZE: {label['pack_size']:<48} │
├────────────────────────────────────────────────────────────────┤
│  SHIP TO:   {label['ship_to_company']:<48} │
│             {label['ship_to_address'][:48]:<48} │
│             {label['ship_to_city_state_zip']:<48} │
├────────────────────────────────────────────────────────────────┤
│  DELIVERY:  {label['delivery_date']:<48} │
└────────────────────────────────────────────────────────────────┘
                """)
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    get_non_shipped_shipments()

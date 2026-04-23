import os
import sys

# Add the parent directory to the path
sys.path.insert(0, os.path.dirname(__file__))

from app.config.database import SessionLocal
from app.models import Shipment
import json

def split_by_pack_size(quantity, pack_size=1):
    """
    Split a quantity into boxes by pack size.
    
    Args:
        quantity: Total quantity to split
        pack_size: Items per box (default 1)
        
    Returns:
        List of quantities per box
        
    Example:
        quantity=50, pack_size=6 -> [6, 6, 6, 6, 6, 6, 6, 6, 2]
    """
    if not pack_size or pack_size == 0:
        pack_size = 1
    
    full_boxes = quantity // pack_size
    remainder = quantity % pack_size
    
    labels = [pack_size] * full_boxes
    if remainder > 0:
        labels.append(remainder)
    
    return labels

def generate_labels():
    """Generate labels for shipments that are ready to ship"""
    db = SessionLocal()
    try:
        # Get shipments with "Ready for shipment" status ONLY
        # Status 60 = Ready for shipment in MRPeasy
        shipments = db.query(Shipment).filter(
            Shipment.status == 60  # Only "Ready for shipment"
        ).all()
        
        print(f"🔄 Generating labels for shipments...")
        print(f"Found {len(shipments)} shipments with 'Ready for shipment' status\n")
        print("=" * 100)
        
        total_labels = 0
        
        for shipment in shipments:
            # Parse the products JSON
            products = []
            if shipment.products:
                try:
                    products = json.loads(shipment.products) if isinstance(shipment.products, str) else shipment.products
                except json.JSONDecodeError:
                    print(f"⚠️  Warning: Could not parse products for shipment {shipment.code}")
                    continue
            
            # Generate labels for this shipment
            shipment_labels = []
            label_counter = 1
            
            print(f"\n🏷️  Shipment: {shipment.code}")
            print(f"   Status: {shipment.status_txt} (ID: {shipment.status})")
            print(f"   Customer Order: {shipment.customer_order_code}")
            print(f"   Delivery Date: {shipment.delivery_date}")
            print(f"   Products: {len(products)}")
            
            for product in products:
                item_code = product.get('item_code', 'N/A')
                quantity_picked = product.get('quantity_picked', 0)
                lot_code = product.get('lot_code', 'N/A')
                
                # For now, assume pack size is 1 (since we don't have pack_size in the data yet)
                # TODO: Add pack_size field to stock_items table
                pack_size = 1
                
                # Split quantity into boxes
                box_quantities = split_by_pack_size(quantity_picked, pack_size)
                total_boxes = len(box_quantities)
                
                # Generate labels for each box
                for box_num, box_qty in enumerate(box_quantities, 1):
                    label = {
                        'shipment_code': shipment.code,
                        'customer_order': shipment.customer_order_code,
                        'item_code': item_code,
                        'lot_code': lot_code,
                        'box_number': box_num,
                        'total_boxes': total_boxes,
                        'quantity': box_qty,
                        'delivery_date': str(shipment.delivery_date) if shipment.delivery_date else 'N/A'
                    }
                    shipment_labels.append(label)
                    label_counter += 1
            
            # Print labels for this shipment
            print(f"\n   📋 LABELS GENERATED: {len(shipment_labels)} labels")
            print("   " + "-" * 96)
            for i, label in enumerate(shipment_labels, 1):
                print(f"   Label #{i:03d} | Item: {label['item_code']} | " 
                      f"Box: {label['box_number']}/{label['total_boxes']} | "
                      f"Qty: {label['quantity']} | Lot: {label['lot_code']}")
            
            total_labels += len(shipment_labels)
            print("-" * 100)
        
        print(f"\n📊 LABELING SUMMARY:")
        print(f"   Shipments to Process: {len(shipments)}")
        print(f"   Total Labels to Print: {total_labels}")
        
        if shipments and total_labels > 0:
            # Show a sample label format
            print(f"\n📄 SAMPLE LABEL (First shipment, first label):")
            print("=" * 100)
            # sample_shipment = shipments[0]
            # Would format the actual label here
        
    finally:
        db.close()

if __name__ == "__main__":
    generate_labels()

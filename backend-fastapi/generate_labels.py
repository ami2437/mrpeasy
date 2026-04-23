"""
Labeling System - Split shipment items by pack size
"""
import sys
from pathlib import Path

# Add the parent directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.mrpeasy_client import mrpeasy_client
import json

def calculate_boxes(quantity, pack_size):
    """Calculate number of boxes and remaining items"""
    if pack_size <= 0:
        pack_size = 1
    
    full_boxes = quantity // pack_size
    remaining = quantity % pack_size
    
    boxes = []
    
    # Add full boxes
    for i in range(full_boxes):
        boxes.append({
            'box_number': i + 1,
            'quantity': pack_size
        })
    
    # Add partial box if there's a remainder
    if remaining > 0:
        boxes.append({
            'box_number': full_boxes + 1,
            'quantity': remaining
        })
    
    return boxes

def generate_labels(label_mode='individual'):
    """
    Generate labels for shipment items with pack size splitting
    
    Args:
        label_mode: 'individual' - one label per box
                   'grouped' - one label per unique quantity (groups boxes with same qty)
    """
    try:
        print("🔄 Fetching shipments from MRPeasy API...")
        shipments = mrpeasy_client.get_shipments()
        
        if not shipments:
            print("⚠️  No shipments found")
            return
        
        # Find ready shipments
        ready_shipments = [s for s in shipments if 'ready' in s.get('status_txt', '').lower()]
        
        if not ready_shipments:
            print("⚠️  No shipments ready for shipment")
            return
        
        # Use the first ready shipment (SH215598)
        shipment = ready_shipments[0]
        
        print(f"\n📦 SHIPMENT: {shipment.get('code')}")
        print(f"   Customer Order: {shipment.get('customer_order_code')}")
        print(f"   Status: {shipment.get('status_txt')}")
        print(f"   Label Mode: {label_mode.upper()}")
        print("\n" + "="*100)
        
        # Get pack sizes for each item
        pack_sizes = {
            '51753': 40,   # User specified pack size
            '51754': 150   # User specified pack size
        }
        
        all_labels = []
        
        print("\n📋 GENERATING LABELS:\n")
        
        for product in shipment.get('products', []):
            item_code = product.get('item_code')
            item_title = product.get('item_title')
            quantity = product.get('quantity_booked', 0)
            lot_code = product.get('lot_code')
            
            # Get pack size (default to 1 if not specified)
            pack_size = pack_sizes.get(item_code, 1)
            
            print(f"\n{'='*100}")
            print(f"Item Code: {item_code}")
            print(f"Description: {item_title}")
            print(f"Total Quantity: {quantity} pieces")
            print(f"Pack Size: {pack_size} pieces/box")
            print(f"Lot: {lot_code}")
            print(f"\n{'─'*100}")
            
            # Calculate boxes
            boxes = calculate_boxes(quantity, pack_size)
            
            print(f"\n📦 BOX BREAKDOWN: {len(boxes)} box(es)\n")
            
            if label_mode == 'individual':
                # Individual mode: one label per box
                for box in boxes:
                    label = {
                        'shipment_code': shipment.get('code'),
                        'customer_order': shipment.get('customer_order_code'),
                        'item_code': item_code,
                        'item_title': item_title,
                        'lot_code': lot_code,
                        'box_number': box['box_number'],
                        'total_boxes': len(boxes),
                        'quantity_in_box': box['quantity'],
                        'total_quantity': quantity,
                        'label_type': 'individual'
                    }
                    all_labels.append(label)
                    
                    print(f"   Box {box['box_number']} of {len(boxes)}: {box['quantity']} pieces")
            
            else:
                # Grouped mode: one label per unique quantity
                qty_groups = {}
                for box in boxes:
                    qty = box['quantity']
                    if qty not in qty_groups:
                        qty_groups[qty] = []
                    qty_groups[qty].append(box['box_number'])
                
                for qty, box_numbers in qty_groups.items():
                    label = {
                        'shipment_code': shipment.get('code'),
                        'customer_order': shipment.get('customer_order_code'),
                        'item_code': item_code,
                        'item_title': item_title,
                        'lot_code': lot_code,
                        'box_count': len(box_numbers),
                        'box_numbers': box_numbers,
                        'total_boxes': len(boxes),
                        'quantity_in_box': qty,
                        'total_quantity': quantity,
                        'label_type': 'grouped'
                    }
                    all_labels.append(label)
                    
                    print(f"   {len(box_numbers)} box(es) of {qty} pieces each (Boxes: {', '.join(map(str, box_numbers))})")
            
            print(f"\n{'─'*100}")
        
        # Summary
        print(f"\n\n{'='*100}")
        print(f"📊 LABEL SUMMARY:")
        print(f"   Total Items: {len(shipment.get('products', []))}")
        print(f"   Total Labels: {len(all_labels)}")
        print(f"{'='*100}\n")
        
        # Print all labels in detail
        print("\n📄 COMPLETE LABEL LIST:\n")
        for idx, label in enumerate(all_labels, 1):
            print(f"\n┌─ LABEL #{idx} " + "─"*85)
            print(f"│ Shipment: {label['shipment_code']} | Order: {label['customer_order']}")
            print(f"│ Item: {label['item_code']} - {label['item_title'][:50]}")
            print(f"│ Lot: {label['lot_code']}")
            
            if label['label_type'] == 'individual':
                print(f"│ Box: {label['box_number']} of {label['total_boxes']}")
                print(f"│ Quantity: {label['quantity_in_box']} pieces (Total: {label['total_quantity']})")
            else:
                print(f"│ Boxes: {label['box_count']} of {label['total_boxes']} (Boxes: {', '.join(map(str, label['box_numbers']))})")
                print(f"│ Quantity per box: {label['quantity_in_box']} pieces (Total: {label['total_quantity']})")
            
            print(f"└" + "─"*99)
        
        # Save labels to JSON file
        filename = f'labels_output_{label_mode}.json'
        with open(filename, 'w') as f:
            json.dump(all_labels, f, indent=2)
        
        print(f"\n✅ Labels saved to: {filename}")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    import sys
    
    # Get label mode from command line argument
    label_mode = 'individual'  # default
    if len(sys.argv) > 1:
        mode_arg = sys.argv[1].lower()
        if mode_arg in ['individual', 'grouped']:
            label_mode = mode_arg
        else:
            print("❌ Invalid mode. Use 'individual' or 'grouped'")
            print("\nUsage:")
            print("  python generate_labels.py individual   # One label per box")
            print("  python generate_labels.py grouped      # One label per unique quantity")
            sys.exit(1)
    
    generate_labels(label_mode)

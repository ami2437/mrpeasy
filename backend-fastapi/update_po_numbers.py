"""
Update PO numbers in existing shipment_boxes records
"""
import sqlite3
import sys
sys.path.insert(0, '.')

from app.services.mrpeasy_client import mrpeasy_client

db_path = 'mrpeasy.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    # Get all distinct shipments with null po_number
    cursor.execute('SELECT DISTINCT shipment_code FROM shipment_boxes WHERE po_number IS NULL')
    shipment_codes = [row[0] for row in cursor.fetchall()]
    
    print(f"Found {len(shipment_codes)} shipments with missing PO numbers")
    
    for shipment_code in shipment_codes:
        try:
            # Get shipment details
            shipments = mrpeasy_client.get_shipments()
            shipment = next((s for s in shipments if s.get('code') == shipment_code), None)
            
            if shipment and shipment.get('customer_order_id'):
                customer_order = mrpeasy_client.get_customer_order(shipment.get('customer_order_id'))
                po_number = customer_order.get('reference') or customer_order.get('code')
                
                # Update all records for this shipment
                cursor.execute('UPDATE shipment_boxes SET po_number = ? WHERE shipment_code = ?',
                              (po_number, shipment_code))
                conn.commit()
                print(f"✅ Updated {shipment_code} with PO #: {po_number}")
            else:
                print(f"⚠️ Could not find shipment {shipment_code}")
        except Exception as e:
            print(f"❌ Error updating {shipment_code}: {e}")
    
    print("\n✅ All PO numbers updated!")

finally:
    conn.close()

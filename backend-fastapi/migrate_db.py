"""
Add po_number column to shipment_boxes table
"""
import sqlite3

db_path = 'mrpeasy.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    # Check if column exists
    cursor.execute("PRAGMA table_info(shipment_boxes)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'po_number' not in columns:
        print("Adding po_number column to shipment_boxes table...")
        cursor.execute("ALTER TABLE shipment_boxes ADD COLUMN po_number TEXT")
        conn.commit()
        print("✅ Column added successfully!")
    else:
        print("po_number column already exists")

    if 'customer_name' not in columns:
        print("Adding customer_name column to shipment_boxes table...")
        cursor.execute("ALTER TABLE shipment_boxes ADD COLUMN customer_name TEXT")
        conn.commit()
        print("✅ Column added successfully!")
    else:
        print("customer_name column already exists")

    if 'shipping_address' not in columns:
        print("Adding shipping_address column to shipment_boxes table...")
        cursor.execute("ALTER TABLE shipment_boxes ADD COLUMN shipping_address TEXT")
        conn.commit()
        print("✅ Column added successfully!")
    else:
        print("shipping_address column already exists")

    if 'job_number' not in columns:
        print("Adding job_number column to shipment_boxes table...")
        cursor.execute("ALTER TABLE shipment_boxes ADD COLUMN job_number TEXT")
        conn.commit()
        print("✅ Column added successfully!")
    else:
        print("job_number column already exists")

    if 'delivery_date' not in columns:
        print("Adding delivery_date column to shipment_boxes table...")
        cursor.execute("ALTER TABLE shipment_boxes ADD COLUMN delivery_date TEXT")
        conn.commit()
        print("✅ Column added successfully!")
    else:
        print("delivery_date column already exists")
except Exception as e:
    print(f"❌ Error: {e}")
finally:
    conn.close()

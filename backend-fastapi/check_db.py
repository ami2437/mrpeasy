import sqlite3
import os

# Database location - SQLAlchemy uses sqlite:///./mrpeasy.db which translates to the current directory
db_path = 'mrpeasy.db'

# Check if database exists
if not os.path.exists(db_path):
    print(f"❌ Database not found at {db_path}")
    print(f"Current directory: {os.getcwd()}")
    print(f"Files in directory: {os.listdir()}")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Get tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print('=== TABLES ===')
for table in tables:
    print(f'  {table[0]}')

print('\n=== SHIPMENT_BOXES TABLE ===')
cursor.execute('SELECT * FROM shipment_boxes')
rows = cursor.fetchall()
if rows:
    print(f'Total records: {len(rows)}\n')
    for row in rows:
        print(f'  Shipment: {row["shipment_code"]}, Item: {row["item_code"]}, Box #{row["box_number"]}, Qty: {row["quantity_in_box"]}, Pack Size: {row["pack_size"]}, Order Line: {row["order_line"]}, Pallet: {row["pallet_number"]}')
else:
    print('No records found')

print('\n=== LABELS TABLE ===')
cursor.execute('SELECT * FROM labels')
rows = cursor.fetchall()
if rows:
    print(f'Total records: {len(rows)}')
    for row in rows:
        print(f'  {dict(row)}')
else:
    print('No records found')

conn.close()

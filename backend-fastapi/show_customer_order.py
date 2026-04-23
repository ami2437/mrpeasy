"""
Script to display customer order data structure from MRPeasy API
"""
import json
from app.services.mrpeasy_client import mrpeasy_client

# Get customer order C89076 (from shipment SH215598)
print("Fetching customer order C89076...")

# First get all customer orders and find the one with code C89076
orders = mrpeasy_client.get_customer_orders()
print(f"Total orders: {len(orders)}")

target_order = None
for order in orders:
    if order.get('code') == 'C89076':
        target_order = order
        break

if target_order:
    print(f"\nFound customer order: {target_order.get('code')}")
    print(f"Order ID: {target_order.get('customer_order_id')}")
    
    print("\n" + "="*80)
    print("FULL CUSTOMER ORDER DATA:")
    print("="*80)
    print(json.dumps(target_order, indent=2))
    
    print("\n" + "="*80)
    print("KEY FIELDS:")
    print("="*80)
    for key in sorted(target_order.keys()):
        value = target_order[key]
        value_type = type(value).__name__
        if isinstance(value, (list, dict)):
            print(f"  {key}: <{value_type} with {len(value)} items>")
        else:
            print(f"  {key}: {value} <{value_type}>")
else:
    print("Customer order C89076 not found")
    print("\nShowing first customer order instead:")
    if orders:
        print(json.dumps(orders[0], indent=2))

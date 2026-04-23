import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()
orders = client.get_customer_orders()
invoices = client.get_invoices()

# Get order IDs with invoices
order_ids_with_invoices = set([inv.get('cust_ord_id') for inv in invoices if inv.get('cust_ord_id')])

print("=" * 80)
print("ALL ORDERS WITH SHIPPED ITEMS (NO INVOICE)")
print("=" * 80)

uninvoiced_with_shipments = []

for order in orders:
    ord_id = order.get('cust_ord_id')
    products = order.get('products', [])
    
    has_shipped = False
    
    for p in products:
        shipped = p.get('shipped', 0)
        if shipped > 0:
            has_shipped = True
            break
    
    # Check if order has invoice
    has_invoice = ord_id in order_ids_with_invoices
    
    # Count orders with shipments but NO invoice
    if has_shipped and not has_invoice:
        uninvoiced_with_shipments.append(ord_id)
        print(f'Order {ord_id} ({order.get("code")}) - {order.get("customer_name")} - Status: {order.get("status_txt")}')

print("\n" + "=" * 80)
print(f"TOTAL UNINVOICED ORDERS WITH SHIPMENTS: {len(uninvoiced_with_shipments)}")
print(f"Order IDs: {uninvoiced_with_shipments}")
print("=" * 80)

# Now check how many orders exist total
print(f"\nTotal orders in system: {len(orders)}")
print(f"Total invoices in system: {len(invoices)}")
print(f"Unique orders with invoices: {len(order_ids_with_invoices)}")

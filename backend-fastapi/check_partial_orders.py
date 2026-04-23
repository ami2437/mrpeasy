import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()
orders = client.get_customer_orders()
invoices = client.get_invoices()

# Get order IDs with invoices
order_ids_with_invoices = set([inv.get('cust_ord_id') for inv in invoices if inv.get('cust_ord_id')])

print("=" * 80)
print("SEARCHING FOR ORDERS WITH PARTIAL SHIPMENTS")
print("=" * 80)

partially_shipped_orders = []
partially_shipped_uninvoiced = []

for order in orders:
    ord_id = order.get('cust_ord_id')
    products = order.get('products', [])
    
    has_partial = False
    has_shipped = False
    
    for p in products:
        qty = p.get('quantity', 0)
        shipped = p.get('shipped', 0)
        
        if shipped > 0:
            has_shipped = True
        
        if 0 < shipped < qty:
            has_partial = True
    
    if has_partial:
        partially_shipped_orders.append(ord_id)
        has_invoice = ord_id in order_ids_with_invoices
        
        print(f'\nOrder {ord_id} ({order.get("code")}) - {order.get("customer_name")}')
        print(f'  Status: {order.get("status_txt")}')
        print(f'  Has Invoice: {"YES" if has_invoice else "NO"}')
        
        for p in products:
            qty = p.get('quantity', 0)
            shipped = p.get('shipped', 0)
            if shipped > 0:
                status = 'PARTIAL' if shipped < qty else 'FULL'
                print(f'    {p.get("item_code")}: qty={qty}, shipped={shipped}, status={status}')
        
        if not has_invoice:
            partially_shipped_uninvoiced.append(ord_id)

print("\n" + "=" * 80)
print(f"SUMMARY:")
print(f"  Total orders with partial shipments: {len(partially_shipped_orders)}")
print(f"  Partially shipped WITHOUT invoice: {len(partially_shipped_uninvoiced)}")
print(f"  Order IDs: {partially_shipped_uninvoiced}")
print("=" * 80)

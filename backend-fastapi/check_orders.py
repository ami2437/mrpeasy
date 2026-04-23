import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()
orders = client.get_customer_orders()

target_ids = [109, 108, 107, 84, 83, 15, 16, 12, 13, 18, 19]

for order in orders:
    ord_id = order.get('cust_ord_id')
    if ord_id in target_ids:
        print(f'\n=== Order {ord_id} ({order.get("code")}) - {order.get("customer_name")} ===')
        products = order.get('products', [])
        
        has_partial = False
        for p in products:
            qty = p.get('quantity', 0)
            shipped = p.get('shipped', 0)
            
            if 0 < shipped < qty:
                status = 'PARTIAL'
                has_partial = True
            elif shipped >= qty:
                status = 'FULL'
            else:
                status = 'NONE'
            
            if shipped > 0:
                print(f'  {p.get("item_code")}: qty={qty}, shipped={shipped}, status={status}')
        
        if has_partial:
            print(f'  *** ORDER HAS PARTIAL SHIPMENTS ***')

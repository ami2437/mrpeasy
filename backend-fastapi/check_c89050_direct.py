import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()

# Get C89050 order directly from API
all_orders = client.get_customer_orders()
c89050 = next((o for o in all_orders if o.get('code') == 'C89050'), None)

if c89050:
    print("C89050 Direct from API:")
    print(f"  Invoice Status: {c89050.get('invoice_status')} (type: {type(c89050.get('invoice_status'))})")
    print(f"  Is it in ['10', '20']? {str(c89050.get('invoice_status')) in ['10', '20']}")
    print(f"  Products: {c89050.get('products', [])}")

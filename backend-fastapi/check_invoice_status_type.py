import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()
orders = client.get_customer_orders()

# Check data type of invoice_status
print("Checking invoice_status data types:")
for order in orders[:5]:
    inv_status = order.get('invoice_status')
    print(f"Order {order.get('code')}: invoice_status = {repr(inv_status)} (type: {type(inv_status).__name__})")

# Check all unique values
all_statuses = set()
for order in orders:
    all_statuses.add(repr(order.get('invoice_status')))

print(f"\nAll unique invoice_status values: {sorted(all_statuses)}")

# Try filtering with int
int_filter = [o for o in orders if o.get('invoice_status') in [10, 20]]
print(f"\nFiltered with [10, 20] (int): {len(int_filter)} orders")

# Try filtering with str
str_filter = [o for o in orders if o.get('invoice_status') in ['10', '20']]
print(f"Filtered with ['10', '20'] (str): {len(str_filter)} orders")

# Try filtering invoice_status == 10
eq_filter = [o for o in orders if o.get('invoice_status') == 10]
print(f"Filtered with == 10: {len(eq_filter)} orders")

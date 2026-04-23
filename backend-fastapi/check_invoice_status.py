import sys
sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import MRPeasyAPIClient

client = MRPeasyAPIClient()
orders = client.get_customer_orders()

print("=" * 80)
print("INVOICE STATUS ANALYSIS")
print("=" * 80)

status_counts = {}
status_examples = {}

for order in orders:
    ord_id = order.get('cust_ord_id')
    invoice_status = order.get('invoice_status')
    
    # Count by status
    if invoice_status not in status_counts:
        status_counts[invoice_status] = 0
        status_examples[invoice_status] = []
    
    status_counts[invoice_status] += 1
    
    # Keep first 3 examples for each status
    if len(status_examples[invoice_status]) < 3:
        products = order.get('products', [])
        has_shipped = any(p.get('shipped', 0) > 0 for p in products)
        status_examples[invoice_status].append({
            'id': ord_id,
            'code': order.get('code'),
            'customer': order.get('customer_name'),
            'status': order.get('status_txt'),
            'has_shipped': has_shipped
        })

# Display results
for inv_status in sorted(status_counts.keys()):
    print(f"\nInvoice Status: {inv_status}")
    print(f"  Count: {status_counts[inv_status]}")
    print(f"  Examples:")
    for ex in status_examples[inv_status]:
        print(f"    Order {ex['id']} ({ex['code']}) - {ex['customer']}")
        print(f"      Status: {ex['status']}, Has Shipped: {ex['has_shipped']}")

print("\n" + "=" * 80)
print("SUMMARY:")
for inv_status in sorted(status_counts.keys()):
    print(f"  Invoice Status {inv_status}: {status_counts[inv_status]} orders")
print("=" * 80)

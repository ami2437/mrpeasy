import json
from app.services.mrpeasy_client import mrpeasy_client

# Get sample invoice to see what fields it has
invoices = mrpeasy_client.get_invoices()
if invoices:
    print('Sample Invoice Fields:')
    first_inv = invoices[0]
    for field, value in first_inv.items():
        if not isinstance(value, (list, dict)):
            print(f'  {field}: {value}')
        elif isinstance(value, list):
            if len(value) > 0 and isinstance(value[0], dict):
                print(f'  {field}: [list of {len(value)} dicts with keys: {list(value[0].keys())[:5]}...]')
            else:
                print(f'  {field}: [list of {len(value)} items]')
        elif isinstance(value, dict):
            print(f'  {field}: dict with keys: {list(value.keys())[:5]}...')
    
    print('\nChecking for shipment-related fields...')
    for field in first_inv.keys():
        if 'shipment' in field.lower():
            print(f'  Found: {field} = {first_inv[field]}')
        
else:
    print('No invoices found')

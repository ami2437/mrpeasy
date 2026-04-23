import requests
import json

# Prepare the product configs as the frontend would send
product_configs = {
    "test_1_bolt-0": {
        "item_code": "test_1_bolt",
        "order_line": "1",
        "pack_size": 1,
        "label_mode": "individual"
    },
    "test_1_bolt-1": {
        "item_code": "test_1_bolt",
        "order_line": "1",
        "pack_size": 1,
        "label_mode": "individual"
    },
    "test_1_bolt-2": {
        "item_code": "test_1_bolt",
        "order_line": "2",
        "pack_size": 1,
        "label_mode": "individual"
    }
}

print("Sending POST request to /api/labels/generate/SH215601")
print("Product configs:", json.dumps(product_configs, indent=2))
print("\n")

# Make request
response = requests.post(
    'http://localhost:8000/api/labels/generate/SH215601?label_mode=individual',
    json=product_configs
)

print("Response status:", response.status_code)
if response.status_code == 200:
    data = response.json()
    print(f"Total labels generated: {data.get('total_labels')}")
    print("\nLabels:")
    for label in data.get('labels', []):
        print(f"  - Item: {label['item_code']}, Qty: {label['total_quantity']}, Lot: {label['lot_code']}, Box: {label.get('box_number', 'N/A')}/{label.get('total_boxes', 'N/A')}")
else:
    print("Error:", response.text)

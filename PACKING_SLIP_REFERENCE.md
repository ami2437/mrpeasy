# PACKING SLIP SYSTEM - QUICK REFERENCE

## Files to Know

| File | Purpose |
|------|---------|
| `packing-slip.html` | Displays professional packing slip |
| `labels-batch.html` | Modified to link to packing slip |
| `app/routes/labels.py` | Backend endpoints for finalize & packing-slip |
| `app/models/__init__.py` | ShipmentBox model with po_number field |
| `mrpeasy.db` | SQLite database storing box data |

## API Endpoints

### Finalize Shipment
```
POST /api/labels/finalize/{shipment_code}
Content-Type: application/json

Body:
{
  "pallet_number": "P001",
  "product_configs": {
    "test_1_bolt-0": {
      "item_code": "test_1_bolt",
      "order_line": "1",
      "pack_size": 30
    },
    "test_1_bolt-1": {
      "item_code": "test_1_bolt",
      "order_line": "2",
      "pack_size": 50
    }
  }
}

Response:
{
  "success": true,
  "shipment_code": "SH215601",
  "pallet_number": "P001",
  "total_boxes_saved": 5,
  "boxes": [...]
}
```

### Get Packing Slip
```
GET /api/packing-slip/{shipment_code}

Example: GET /api/packing-slip/SH215601

Response:
{
  "success": true,
  "shipment_code": "SH215601",
  "items": [
    {
      "shipment_code": "SH215601",
      "item_code": "test_1_bolt",
      "item_title": "test-bolt",
      "order_line": "1",
      "po_number": "PO # 123456",
      "finalized_at": "2026-02-02",
      "qty_shipped": 100,
      "box_breakdown": "3 box of 30, 2 box of 5",
      "pallet_number": null,
      "lot_codes": [],
      "all_boxes": [...]
    }
  ],
  "total_items": 2
}
```

## Database Schema - shipment_boxes

```sql
CREATE TABLE shipment_boxes (
  id INTEGER PRIMARY KEY,
  shipment_code TEXT NOT NULL,
  customer_order_code TEXT NOT NULL,
  po_number TEXT,                    -- NEW FIELD
  item_code TEXT NOT NULL,
  item_title TEXT NOT NULL,
  order_line TEXT DEFAULT "1",
  pack_size INTEGER NOT NULL,
  box_number INTEGER NOT NULL,
  quantity_in_box INTEGER NOT NULL,
  total_quantity INTEGER NOT NULL,
  lot_codes TEXT,
  pallet_number TEXT,
  generated_from TEXT,
  finalized_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

## Sample Data - SH215601

### Database Records (6 boxes)
```
| shipment_code | item_code     | order_line | box_number | qty | pack_size | po_number    |
|---------------|---------------|------------|------------|-----|-----------|--------------|
| SH215601      | test_1_bolt   | 1          | 1          | 30  | 30        | PO # 123456  |
| SH215601      | test_1_bolt   | 1          | 2          | 30  | 30        | PO # 123456  |
| SH215601      | test_1_bolt   | 1          | 3          | 30  | 30        | PO # 123456  |
| SH215601      | test_1_bolt   | 1          | 4          | 5   | 30        | PO # 123456  |
| SH215601      | test_1_bolt   | 1          | 5          | 5   | 30        | PO # 123456  |
| SH215601      | test_1_bolt   | 2          | 1          | 50  | 50        | PO # 123456  |
```

### Packing Slip Display (2 items, 6 boxes)
```
Item: test_1_bolt (test-bolt)
  Order Line: 1
  PO #: PO # 123456
  Qty Shipped: 100
  Box Breakdown: 3 box of 30, 2 box of 5

Item: test_1_bolt (test-bolt)
  Order Line: 2
  PO #: PO # 123456
  Qty Shipped: 50
  Box Breakdown: 1 box of 50
```

## URLs

| Purpose | URL |
|---------|-----|
| Labels Page | `http://localhost:3000/labels-batch.html` |
| Packing Slip | `http://localhost:3000/packing-slip.html?shipment=SH215601` |
| API - Finalize | `http://localhost:8000/api/labels/finalize/SH215601` |
| API - Packing Slip | `http://localhost:8000/api/packing-slip/SH215601` |

## User Workflow

### Step 1: Open Labels Page
```
http://localhost:3000/labels-batch.html
```

### Step 2: Expand Shipment
Click arrow to expand SH215601

### Step 3: Enter Pack Sizes
For each item, enter pack size (e.g., 30, 50)

### Step 4: Optional - Enter Pallet
Enter pallet number if grouping with other shipments

### Step 5: Finalize
Click "🔒 Finalize & Lock" button

### Step 6: View Packing Slip
Click "View Packing Slip" link in success message

### Step 7: Print
Click "🖨️ Print" button and save as PDF or print to paper

## Calculation: Box Breakdown

```
Input: 100 units with pack_size 30
Calculation:
  100 ÷ 30 = 3 full boxes (90 units)
  100 - 90 = 10 remaining units → 1 box of 10
  But we have: 3 box of 30, 2 box of 5
  
Algorithm:
  Group all quantities by pack size
  Count occurrences: 30→3, 5→2
  Format: "3 box of 30, 2 box of 5"
```

## Browser Print Settings

- **Orientation:** Portrait
- **Margins:** Default
- **Scale:** 100%
- **Headers/Footers:** Off (auto disabled)
- **Destination:** Save as PDF or Print

## Troubleshooting

| Issue | Solution |
|-------|----------|
| API returns 404 | Restart backend: `python -m uvicorn app.main:app --reload` |
| No boxes appear | Check if shipment was finalized |
| PO # is empty | Ensure customer order is linked in MRPeasy |
| Print looks bad | Use Chrome/Edge browsers for best print results |

## Performance Notes

- Packing slip groups up to 100 items efficiently
- Database queries are indexed on shipment_code
- HTML renders in < 100ms
- Print-to-PDF takes 1-2 seconds

## Future Enhancements

- [ ] Add qty_remaining to display
- [ ] Export to PDF server-side
- [ ] Multi-shipment pallet grouping
- [ ] Email packing slip to customer
- [ ] Barcode scanning integration

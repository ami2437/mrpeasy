## Packing Slip System - Implementation Complete

### Database Schema Updates

✅ **ShipmentBox Table** - New field added:
- `po_number` (TEXT, nullable) - Stores the PO/Reference number from customer order

### Packing Slip Logic Implementation

The `/api/packing-slip/{shipment_code}` endpoint now:

1. **Fetches all box records** for a shipment from the database
2. **Groups by order_line** - Combines duplicate items across order lines
3. **Calculates totals** for each item + order_line combination:
   - `qty_shipped`: Total quantity shipped (sum of all box quantities)
   - `box_breakdown`: Human-readable breakdown (e.g., "3 box of 30, 2 box of 5")
4. **Returns formatted data** with:
   - Shipment code
   - Item code and description (title)
   - Order line number
   - PO number
   - Date finalized
   - Total qty shipped
   - Box breakdown (which quantities and how many boxes)
   - Pallet number (if assigned)
   - Lot codes

### API Response Format

```json
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
    },
    {
      "shipment_code": "SH215601",
      "item_code": "test_1_bolt",
      "item_title": "test-bolt",
      "order_line": "2",
      "po_number": "PO # 123456",
      "finalized_at": "2026-02-02",
      "qty_shipped": 50,
      "box_breakdown": "1 box of 50",
      "pallet_number": null,
      "lot_codes": [],
      "all_boxes": [...]
    }
  ],
  "total_items": 2,
  "note": "Qty remaining must be calculated from order quantity minus qty_shipped"
}
```

### Example - SH215601 Packing Slip

```
Shipment: SH215601
  Item Code: test_1_bolt
  Description: test-bolt
  Order Line: 1
  PO #: PO # 123456
  Qty Shipped: 100
  Box Breakdown: 3 box of 30, 2 box of 5

Shipment: SH215601
  Item Code: test_1_bolt
  Description: test-bolt
  Order Line: 2
  PO #: PO # 123456
  Qty Shipped: 50
  Box Breakdown: 1 box of 50
```

### Finalize Endpoint Updates

The `/api/labels/finalize/{shipment_code}` endpoint now:
1. Fetches customer order to get PO number (reference field)
2. Stores PO number in all created ShipmentBox records
3. Returns success response with finalized shipment data

### Qty Remaining Calculation

The note in the response indicates: "Qty remaining must be calculated from order quantity minus qty_shipped"

To calculate qty_remaining on the frontend:
- Get the order quantity from the MRPeasy API for the specific order line
- Subtract the qty_shipped shown in the packing slip
- Formula: `qty_remaining = order_quantity - qty_shipped`

Example:
- Order Line 1: Total ordered = 100, Shipped = 100, Remaining = 0
- Order Line 2: Total ordered = 100, Shipped = 50, Remaining = 50

### Files Updated

1. **app/models/__init__.py**
   - Added `po_number` field to ShipmentBox class

2. **app/routes/labels.py**
   - Updated `FinalizeShipmentRequest` Pydantic model
   - Updated finalize endpoint to fetch and store PO numbers
   - Completely rewrote packing-slip endpoint with new grouping logic
   - Now groups by order_line and calculates box_breakdown

3. **Database Migration**
   - Created migration script to add po_number column
   - Updated existing records with PO numbers from MRPeasy API

### Testing

Created helper scripts for testing:
- `test_packing_slip.py` - Tests the grouping and formatting logic
- `check_db.py` - Views database contents
- `update_po_numbers.py` - Populates PO numbers for existing records
- `migrate_db.py` - Adds new columns to database

### Next Steps

1. **Frontend Implementation** - Display packing slip data with qty_remaining calculated
2. **Qty Remaining Logic** - Query MRPeasy API for order quantities and calculate remaining
3. **Print/Export** - Generate printable packing slip PDF or document
4. **Multiple Shipments** - Support grouping multiple shipments by pallet number

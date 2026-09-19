# Packing Slip & Shipment Labeling System

## Overview

When a shipment is finalized, the system captures the box/pack configuration,
locks it into the database, and generates a professional, printable packing
slip from that stored data. Key properties:

- Groups items by `order_line`, combining duplicate item rows and showing a
  human-readable box breakdown (e.g. "3 box of 30, 2 box of 5").
- Locks pack-size configuration on finalize — inputs are disabled afterwards
  and the finalize endpoint is idempotent-safe (no accidental re-writes).
- Packing slip generation always reads from the locked database records, not
  from live/recalculated values.

## Database schema

### `shipment_boxes`
One record per physical box.

| Column | Notes |
|---|---|
| `shipment_code` | Shipment reference |
| `customer_order_code` | Linked customer order |
| `item_code` / `item_title` | Product code / description |
| `order_line` | Order line number (grouping key) |
| `po_number` | PO/reference number, captured from the customer order at finalize time |
| `pack_size` / `box_number` / `quantity_in_box` | Box configuration |
| `total_quantity` | Total quantity shipped for the item + order line |
| `lot_codes` | JSON array of lot codes |
| `pallet_number` | Optional, for grouping multiple shipments |
| `finalized_at` | Timestamp when locked |

### `labels`
Historical/audit table for generated labels (label_id, shipment_code,
item_code, box_number, quantity, timestamps) — reserved for future label
reprint/audit features.

## Backend API

### `POST /api/labels/finalize/{shipment_code}`
Finalizes and locks shipment box configuration.

Request:
```json
{
  "pallet_number": "PALLET-001",
  "product_configs": {
    "79300-HPC-0": { "item_code": "79300-HPC", "order_line": "1", "pack_size": 35 },
    "79300-HPC-1": { "item_code": "79300-HPC", "order_line": "1", "pack_size": 35 }
  }
}
```

Response:
```json
{
  "success": true,
  "shipment_code": "SH215599",
  "pallet_number": "PALLET-001",
  "total_boxes_saved": 34,
  "boxes": [ ... ]
}
```

Behavior: fetches the PO number from the customer order, computes box counts
from pack size, creates `ShipmentBox` records, and commits.

### `GET /api/packing-slip/{shipment_code}`
Retrieves formatted, grouped packing slip data for display/printing.

Response:
```json
{
  "success": true,
  "shipment_code": "SH215601",
  "items": [
    {
      "item_code": "test_1_bolt",
      "item_title": "test-bolt",
      "order_line": "1",
      "po_number": "PO # 123456",
      "finalized_at": "2026-02-02",
      "qty_shipped": 100,
      "box_breakdown": "3 box of 30, 2 box of 5",
      "pallet_number": null,
      "lot_codes": [],
      "all_boxes": [ ... ]
    }
  ]
}
```

### `GET /api/labels/shipments/ready` and `GET /api/labels/shipments/{code}`
List shipments ready for packing and fetch a single shipment's products
(enriched with `order_line` and `qty_remaining`, derived from the customer
order's source line items).

## Frontend

- `packing-slip.html` — professional printable packing slip: header
  (Shipment #, PO #, Date, Customer), summary stats, itemized table grouped
  by order line, signature lines (Packed/Checked/Shipped by), print button.
- `labels-batch.html` — shipment prep UI: pack-size + pallet number inputs
  per grouped item, "🔒 Finalize & Lock" button that calls the finalize
  endpoint and then links to the generated packing slip.

## Example output

```
PACKING SLIP
Shipment #: SH215601   PO #: PO # 123456   Date: 02/02/2026   Customer: American Traders LLC

SUMMARY: Total Items: 2 | Total Qty Shipped: 150 | Multi-line items: test_1_bolt (2 lines)

Item Code / Description | PO #      | Line | Qty Shipped | Box Breakdown
test_1_bolt / test-bolt | PO#123456 |  1   |     100     | 3 box of 30, 2 box of 5
test_1_bolt / test-bolt | PO#123456 |  2   |      50     | 1 box of 50

Packed By: ________  Checked By: ________  Shipped By: ________
```

## Manual testing

```powershell
# Start backend
cd C:\mrpeasy\backend-fastapi
. .\mrpeasy\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Frontend
# http://localhost:3000/labels-batch.html

# List ready shipments
curl http://localhost:8000/api/labels/shipments/ready

# Get shipment details (order_line + qty_remaining enrichment)
curl http://localhost:8000/api/labels/shipments/SH215599

# Finalize (PowerShell)
$body = @{
  pallet_number = "PALLET-001"
  product_configs = @{
    "79300-HPC-0" = @{ item_code = "79300-HPC"; order_line = "1"; pack_size = 35 }
  }
} | ConvertTo-Json
curl -X POST -H "Content-Type: application/json" -d $body http://localhost:8000/api/labels/finalize/SH215599

# Read packing slip
curl http://localhost:8000/api/packing-slip/SH215599
```

## Key files

| File | Purpose |
|---|---|
| `frontend/public/packing-slip.html` | Displays the printable packing slip |
| `frontend/public/labels-batch.html` | Finalize/lock UI, links to packing slip |
| `backend-fastapi/app/routes/labels.py` | Finalize & packing-slip endpoints |
| `backend-fastapi/app/models/__init__.py` | `ShipmentBox` / `Label` models |
| `backend-fastapi/mrpeasy.db` | SQLite storage for box/label data |

See [LABEL_TOOLS_ROLLBACK_PLAN](ROLLBACK_PLAN.md) for the data-protection plan
that applied while this feature was built.

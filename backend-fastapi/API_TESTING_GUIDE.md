# API Testing Guide - Packing Slip System

## Quick Start - Test the Full Workflow

### 1. Start Backend
```powershell
cd C:\mrpeasy\backend-fastapi
. .\mrpeasy\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 2. View Frontend
```
http://localhost:3000/labels-batch.html
```

---

## Manual API Testing (PowerShell)

### A. Get Ready Shipments
```powershell
curl http://localhost:8000/api/labels/shipments/ready
```

**Response:**
```json
{
  "shipments": [
    {
      "code": "SH215599",
      "customer_order_code": "C89077",
      "status_txt": "Ready for shipment",
      "products_count": 2
    }
  ]
}
```

---

### B. Get Shipment Details (with order_line enrichment)
```powershell
curl http://localhost:8000/api/labels/shipments/SH215599
```

**Response:**
```json
{
  "success": true,
  "shipment": {
    "code": "SH215599",
    "customer_order_code": "C89077",
    "customer_name": "Hudson Products",
    "reference": "PO # 4134724",
    "products": [
      {
        "item_code": "79300-HPC",
        "item_title": "BOLT_HH_3/4-10x10_316/316L_F593",
        "quantity_booked": 619,
        "lot_code": "L00300",
        "order_line": "1",
        "qty_remaining": 1180
      },
      {
        "item_code": "79300-HPC",
        "item_title": "BOLT_HH_3/4-10x10_316/316L_F593",
        "quantity_booked": 561,
        "lot_code": "L00463",
        "order_line": "1",
        "qty_remaining": 1180
      }
    ]
  }
}
```

**Key Observations:**
- Both products have same item_code + order_line = "1"
- Both have qty_remaining = 1180 (total order qty - already shipped)
- In UI, these will be grouped into ONE row

---

### C. Finalize Shipment Configuration (Save to DB)

```powershell
$body = @{
    pallet_number = "PALLET-001"
    product_configs = @{
        "79300-HPC-0" = @{
            item_code = "79300-HPC"
            order_line = "1"
            pack_size = 35
        }
        "79300-HPC-1" = @{
            item_code = "79300-HPC"
            order_line = "1"
            pack_size = 35
        }
    }
} | ConvertTo-Json

curl -X POST `
  -H "Content-Type: application/json" `
  -d $body `
  http://localhost:8000/api/labels/finalize/SH215599
```

**Response:**
```json
{
  "success": true,
  "shipment_code": "SH215599",
  "pallet_number": "PALLET-001",
  "total_boxes_saved": 34,
  "boxes": [
    {
      "item_code": "79300-HPC",
      "order_line": "1",
      "box_number": 1,
      "quantity": 35
    },
    {
      "item_code": "79300-HPC",
      "order_line": "1",
      "box_number": 2,
      "quantity": 35
    },
    ...
    {
      "item_code": "79300-HPC",
      "order_line": "1",
      "box_number": 34,
      "quantity": 5
    }
  ]
}
```

**What Happened in Database:**
- Created 34 ShipmentBox records
- Each with shipment_code = "SH215599"
- Box numbers 1-33 have 35 items each
- Box 34 has 5 items (remainder: 1180 % 35 = 5)
- All marked with pallet_number = "PALLET-001"

---

### D. Query Packing Slip Data (Read from DB)

```powershell
curl http://localhost:8000/api/packing-slip/SH215599
```

**Response:**
```json
{
  "success": true,
  "shipment_code": "SH215599",
  "items_summary": [
    {
      "item_code": "79300-HPC",
      "item_title": "BOLT_HH_3/4-10x10_316/316L_F593",
      "order_line": "1",
      "pack_size": 35,
      "total_quantity": 1180,
      "total_boxes": 34,
      "pallet_number": "PALLET-001",
      "lot_codes": ["L00300", "L00463"]
    }
  ],
  "all_boxes": [
    {
      "item_code": "79300-HPC",
      "item_title": "BOLT_HH_3/4-10x10_316/316L_F593",
      "order_line": "1",
      "box_number": 1,
      "quantity_in_box": 35,
      "pack_size": 35,
      "lot_codes": ["L00300"],
      "pallet_number": "PALLET-001",
      "finalized_at": "2026-02-01T21:30:00"
    },
    {
      "item_code": "79300-HPC",
      "item_title": "BOLT_HH_3/4-10x10_316/316L_F593",
      "order_line": "1",
      "box_number": 2,
      "quantity_in_box": 35,
      "pack_size": 35,
      "lot_codes": ["L00463"],
      "pallet_number": "PALLET-001",
      "finalized_at": "2026-02-01T21:30:00"
    },
    ...
  ],
  "total_items": 1,
  "total_boxes": 34
}
```

**Usage:** This data drives the packing slip template

---

### E. Generate Labels (From Finalized Data)

```powershell
$body = @{
    "79300-HPC-0" = @{
        item_code = "79300-HPC"
        order_line = "1"
        pack_size = 35
        label_mode = "individual"
    }
    "79300-HPC-1" = @{
        item_code = "79300-HPC"
        order_line = "1"
        pack_size = 35
        label_mode = "individual"
    }
} | ConvertTo-Json

curl -X POST `
  -H "Content-Type: application/json" `
  -d $body `
  "http://localhost:8000/api/labels/generate/SH215599?label_mode=individual"
```

**Response:**
```json
{
  "success": true,
  "shipment_code": "SH215599",
  "label_mode": "individual",
  "total_labels": 34,
  "labels": [
    {
      "shipment_code": "SH215599",
      "customer_order": "C89077",
      "customer_name": "Hudson Products",
      "reference": "PO # 4134724",
      "item_code": "79300-HPC",
      "item_title": "BOLT_HH_3/4-10x10_316/316L_F593",
      "lot_code": "L00300",
      "box_number": 1,
      "total_boxes": 34,
      "quantity_in_box": 35,
      "total_quantity": 1180,
      "label_type": "individual",
      "pack_size": 35
    },
    ...
    {
      "box_number": 34,
      "quantity_in_box": 5,
      ...
    }
  ]
}
```

---

## Database Query Examples

### View All Boxes for a Shipment

```sql
SELECT id, shipment_code, item_code, box_number, quantity_in_box, 
       pallet_number, finalized_at
FROM shipment_boxes
WHERE shipment_code = 'SH215599'
ORDER BY box_number;
```

**Result:**
```
id  | shipment_code | item_code   | box_number | qty | pallet      | finalized_at
----|---------------|-------------|------------|-----|-------------|------------------
1   | SH215599      | 79300-HPC   | 1          | 35  | PALLET-001  | 2026-02-01...
2   | SH215599      | 79300-HPC   | 2          | 35  | PALLET-001  | 2026-02-01...
...
34  | SH215599      | 79300-HPC   | 34         | 5   | PALLET-001  | 2026-02-01...
```

### Check Pallet Consolidation

```sql
SELECT DISTINCT pallet_number, shipment_code, COUNT(*) as boxes
FROM shipment_boxes
WHERE pallet_number = 'PALLET-001'
GROUP BY pallet_number, shipment_code;
```

**Result (if multiple shipments on same pallet):**
```
pallet_number | shipment_code | boxes
--------------|---------------|------
PALLET-001    | SH215599      | 34
PALLET-001    | SH215600      | 28
PALLET-001    | SH215601      | 42
```

---

## Error Cases

### 1. Finalize Non-existent Shipment
```powershell
curl -X POST `
  -H "Content-Type: application/json" `
  -d '{}' `
  http://localhost:8000/api/labels/finalize/SH999999
```

**Response (404):**
```json
{
  "detail": "Shipment SH999999 not found"
}
```

---

### 2. Query Packing Slip Before Finalization
```powershell
curl http://localhost:8000/api/packing-slip/SH215600
```

**Response (404):**
```json
{
  "detail": "No finalized boxes found for SH215600"
}
```

---

### 3. Invalid Product Config
```powershell
# Product index out of range
$body = @{
    pallet_number = "PALLET-002"
    product_configs = @{
        "79300-HPC-999" = @{
            item_code = "79300-HPC"
            pack_size = 35
        }
    }
} | ConvertTo-Json
```

**Response:**
- Backend skips invalid indices
- Returns success with fewer boxes saved

---

## Frontend Interaction Flow

### 1. User Views labels-batch.html
```javascript
// Load shipments
GET /api/labels/shipments/ready

// For each shipment, load details
GET /api/labels/shipments/{code}

// Display grouped by item_code:order_line
```

### 2. User Expands Shipment
```javascript
// Show items table with:
// - Pack size input fields
// - Pallet number input
// - "Finalize & Lock" button
```

### 3. User Finalizes
```javascript
POST /api/labels/finalize/{code}
{
  pallet_number: "PALLET-001",
  product_configs: {...}
}

// On success:
// - Disable all inputs
// - Show "Data locked" message
// - Data now in database
```

### 4. User Generates Labels
```javascript
POST /api/labels/generate/{code}
{...}

// Labels generated from finalized config
```

### 5. View Packing Slip
```javascript
GET /api/packing-slip/{code}

// Display all boxes with pallet info
// Generate PDF with window.print()
```

---

## Production Checklist

- [x] Database tables created
- [x] Backend endpoints implemented
- [x] Frontend UI updated
- [x] Order line enrichment working
- [x] Qty remaining calculated
- [x] Pack size locking working
- [x] Pallet field available
- [x] Finalize endpoint tested
- [x] Packing slip endpoint tested
- [x] Error handling implemented
- [ ] Label generation saves to labels table (TODO)
- [ ] Packing slip HTML template updated (TODO)

---

## Troubleshooting

### Shipments showing no order_line
Check: Customer order must have `source` array with lot codes matching shipment products

### Packing slip returns 404
Check: Must finalize shipment first (POST /finalize endpoint)

### Inputs not disabling after finalize
Check: Browser console for JavaScript errors, ensure finalizeShipment function ran successfully

### Box calculation incorrect
Check: Verify pack_size is correct number, qty_remaining calculation

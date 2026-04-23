# System Architecture Diagrams

## Complete Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         USER WORKFLOW                                       │
└─────────────────────────────────────────────────────────────────────────────┘

START: User opens labels-batch.html
│
├─ Load Ready Shipments
│  └─ GET /api/labels/shipments/ready
│     └─ Returns: Shipment list
│
├─ For each shipment: Load Details
│  └─ GET /api/labels/shipments/{code}
│     └─ Returns: Products with order_line + qty_remaining
│
├─ Display in Table
│  ├─ Group by item_code:order_line (automatic)
│  ├─ Show: Item, Qty, Qty Remaining, Order Line
│  └─ Status: ✅ READY FOR INPUT
│
├─ User Expands Shipment
│  ├─ Sees grouped items
│  ├─ Sees pack size inputs
│  ├─ Sees pallet field
│  └─ Sees "🔒 Finalize & Lock" button
│
├─ User Enters Pack Sizes
│  ├─ Item 79300-HPC order line 1: 35
│  └─ Item 79300-HPC order line 2: 25
│
├─ User Enters Pallet Number (Optional)
│  └─ "PALLET-001"
│
├─ User Clicks "🔒 Finalize & Lock"
│  │
│  └─ Frontend calls:
│     │
│     ├─ POST /api/labels/finalize/{code}
│     │  ├─ Send: pallet_number, product_configs
│     │  │
│     │  └─ Backend:
│     │     ├─ For each product:
│     │     │  ├─ Calculate boxes = qty ÷ pack_size
│     │     │  └─ Create ShipmentBox record for each box
│     │     │
│     │     └─ Save all to database
│     │        └─ Return: success + box_count
│     │
│     └─ Frontend:
│        ├─ Disable all pack size inputs
│        ├─ Disable pallet input
│        ├─ Show "✓ Finalized! Locked in database"
│        └─ Status: ✅ LOCKED (Cannot change)
│
├─ Data Now In Database
│  └─ ShipmentBox table:
│     ├─ Box 1: 35 qty | SH215599 | PALLET-001
│     ├─ Box 2: 35 qty | SH215599 | PALLET-001
│     └─ Box N: X qty  | SH215599 | PALLET-001
│
├─ User Can Generate Labels
│  ├─ Click Printer Icon
│  │
│  └─ POST /api/labels/generate/{code}
│     └─ Returns: Labels based on finalized data
│
├─ User Can View Packing Slip
│  ├─ Navigate to packing-list.html
│  │
│  └─ GET /api/packing-slip/{code}
│     ├─ Query: shipment_boxes table
│     ├─ Group: by item_code
│     └─ Return: items_summary + all_boxes
│
└─ END: Packing Slip Generated from Database
```

---

## Database Schema

```
┌──────────────────────────────────────────────────────────────┐
│                      shipment_boxes                          │
├──────────────────────────────────────────────────────────────┤
│ PK  │ id                    int                              │
│     │ shipment_code         varchar (indexed)                │
│     │ customer_order_code   varchar                          │
│     │ item_code             varchar                          │
│     │ item_title            varchar                          │
│     │ order_line            varchar (default: '1')           │
│     │ pack_size             int                              │
│     │ box_number            int                              │
│     │ quantity_in_box       int                              │
│     │ total_quantity        int                              │
│     │ lot_codes             text (JSON array)                │
│     │ pallet_number         varchar (nullable)               │
│     │ generated_from        varchar                          │
│     │ finalized_at          datetime (indexed)               │
│     │ created_at            datetime                         │
│     │ updated_at            datetime                         │
└──────────────────────────────────────────────────────────────┘

Example Data:
┌────┬──────────────┬──────────────┬──────────────┬──────────┐
│ id │ shipment_cod │ item_code    │ box_number   │ qty      │
│    │ e            │              │              │ in_box   │
├────┼──────────────┼──────────────┼──────────────┼──────────┤
│ 1  │ SH215599     │ 79300-HPC    │ 1            │ 35       │
│ 2  │ SH215599     │ 79300-HPC    │ 2            │ 35       │
│ 3  │ SH215599     │ 79300-HPC    │ 3            │ 35       │
│... │ SH215599     │ 79300-HPC    │ ...          │ ...      │
│34  │ SH215599     │ 79300-HPC    │ 34           │ 5        │
└────┴──────────────┴──────────────┴──────────────┴──────────┘
```

---

## API Call Sequence

```
Browser                          Backend
  │                               │
  ├─────── GET /shipments/ready ──→
  │                               │ Query: Ready shipments
  │←──── Returns: [SH215599, ...] ──│
  │                               │
  ├─ GET /shipments/SH215599 ────→
  │                               │ Query: Shipment + Products
  │                               │ Enrich: order_line + qty_remaining
  │←── Returns: Shipment + 2 products ──
  │                               │
  │ [User enters data]            │
  │ Pack size: 35                 │
  │ Pallet: PALLET-001           │
  │                               │
  ├─ POST /finalize/SH215599 ────→
  │  payload:                     │
  │  ├─ pallet_number            │
  │  └─ product_configs          │ Calculate: 1180 ÷ 35 = 34 boxes
  │                               │ CREATE 34 × ShipmentBox records
  │                               │ SAVE to database
  │←─ Returns: success + 34 boxes ──
  │                               │
  │ [Disable inputs]              │
  │ [Show locked message]         │
  │                               │
  │ [Later...]                    │
  │ ├─ POST /generate/SH215599 ──→
  │ │                             │ Generate labels
  │ │←─ Returns: 34 labels ────────
  │ │ [Print]                    │
  │ │                             │
  │ └─ GET /packing-slip/SH215599→
  │                               │ Query: shipment_boxes table
  │                               │ Group: by item_code
  │←─ Returns: items + boxes ──────
  │   [Display packing slip]      │
  │   [Print]                     │
  │                               │
```

---

## Frontend Component Hierarchy

```
labels-batch.html
│
├─ Header
│  └─ Title: "Label Generator"
│
├─ Controls Section
│  └─ Shipments Table
│     │
│     ├─ Main Row (per shipment)
│     │  ├─ Expand Button (▶)
│     │  ├─ Shipment Code
│     │  ├─ Customer Order
│     │  ├─ Customer Name
│     │  ├─ PO #
│     │  ├─ Status
│     │  ├─ Item Count
│     │  └─ Action Button (🖨️ Print)
│     │
│     └─ Expanded Row (hidden until clicked)
│        │
│        ├─ NEW: Pallet Number Input
│        │  └─ id="pallet-{shipment_code}"
│        │
│        ├─ NEW: Finalize & Lock Button
│        │  └─ onclick="finalizeShipment(index)"
│        │
│        └─ Items Table
│           ├─ Item Code (with lots)
│           ├─ Description
│           ├─ Total Qty
│           ├─ Qty Remaining (color-coded)
│           ├─ Order Line
│           ├─ Pack Size Input
│           └─ Label Mode Selector
│
├─ Labels Container (hidden until generated)
│  └─ Label Cards (printed)
│
└─ JavaScript Functions
   │
   ├─ loadShipments()
   ├─ renderShipmentsTable()
   ├─ toggleItems(index)
   ├─ generateAndPrintShipment(index)
   ├─ generateAllLabels()
   └─ NEW: finalizeShipment(index)
```

---

## Finalization Logic

```
finalizeShipment(index)
│
├─ 1. Collect Data
│  ├─ Get pallet number from input
│  ├─ Get pack sizes from all inputs
│  └─ Group products by item_code:order_line
│
├─ 2. Build Product Configs
│  └─ For each group:
│     ├─ item_code
│     ├─ order_line
│     └─ pack_size
│
├─ 3. Send to Backend
│  └─ POST /api/labels/finalize/{shipment_code}
│     ├─ pallet_number: "PALLET-001"
│     └─ product_configs: {...}
│
├─ 4. Backend Processing
│  ├─ For each product config:
│  │  ├─ Get product details
│  │  ├─ qty_booked = product.quantity_booked
│  │  ├─ pack_size = config.pack_size
│  │  ├─ boxes = calculate_boxes(qty_booked, pack_size)
│  │  │
│  │  └─ For each box:
│  │     ├─ Create ShipmentBox record
│  │     ├─ Set shipment_code
│  │     ├─ Set item_code
│  │     ├─ Set box_number
│  │     ├─ Set quantity_in_box
│  │     ├─ Set order_line
│  │     ├─ Set pallet_number
│  │     └─ Save to DB
│  │
│  └─ COMMIT all records
│
├─ 5. Return Success
│  └─ JSON: {success: true, total_boxes_saved: N}
│
├─ 6. Frontend Updates
│  ├─ Disable all pack size inputs
│  ├─ Disable pallet input
│  ├─ Show confirmation message
│  └─ Status changed: LOCKED
│
└─ END: Data immutable in database
```

---

## Packing Slip Generation Flow

```
GET /api/packing-slip/{shipment_code}
│
├─ 1. Query Database
│  └─ SELECT * FROM shipment_boxes
│     WHERE shipment_code = '{shipment_code}'
│
├─ 2. Group Results
│  └─ By item_code:
│     ├─ 79300-HPC: [Box 1-34]
│
├─ 3. Calculate Summaries
│  └─ For each group:
│     ├─ total_quantity = sum(quantity_in_box)
│     ├─ total_boxes = count(*)
│     ├─ pack_size = first(box.pack_size)
│     └─ pallet_number = first(box.pallet_number)
│
├─ 4. Build Response
│  └─ {
│        items_summary: [{
│          item_code: "79300-HPC",
│          total_quantity: 1180,
│          total_boxes: 34,
│          pallet_number: "PALLET-001"
│        }],
│        all_boxes: [{
│          box_number: 1,
│          quantity_in_box: 35,
│          ...
│        }, ...]
│     }
│
├─ 5. Frontend Uses Data
│  ├─ Display items summary table
│  ├─ Display all boxes detail
│  ├─ Show pallet info
│  └─ Render for printing
│
└─ END: Packing slip generated from database
```

---

## State Transitions

```
                 READY STATE
                     │
         (User enters pack size)
                     │
                     ▼
          INPUT CONFIGURATION STATE
          (User can modify pack sizes)
                     │
        (User clicks "Finalize & Lock")
                     │
                     ▼
     ┌──────────────────────────────────┐
     │   FINALIZATION IN PROGRESS       │
     │  (Sending data to backend)       │
     └──────────────────────────────────┘
                     │
                     ▼
     ┌──────────────────────────────────┐
     │    DATABASE SAVE IN PROGRESS     │
     │  (Backend creating box records)  │
     └──────────────────────────────────┘
                     │
                     ▼
                LOCKED STATE
              (Cannot modify)
              (Data immutable)
              (in database)
                     │
        (User clicks printer icon)
                     │
                     ▼
            LABELS GENERATED
            (from finalized data)
                     │
        (User views packing-list.html)
                     │
                     ▼
         PACKING SLIP GENERATED
         (read from database)
                     │
                     ▼
              READY FOR PRINT
```

---

## Error Handling Flow

```
finalizeShipment(index)
│
├─ TRY
│  ├─ Collect data
│  ├─ POST /finalize
│  │  │
│  │  └─ Backend TRY
│  │     ├─ Validate shipment exists
│  │     ├─ Validate product configs
│  │     ├─ Calculate boxes
│  │     └─ CATCH exceptions
│  │        └─ Return: {detail: error_message}
│  │
│  └─ Check response.success
│     ├─ IF true:
│     │  └─ Show: "✓ Finalized!"
│     │     Disable inputs
│     │
│     └─ IF false:
│        └─ Show error message
│
└─ CATCH
   └─ Show: "Error finalizing shipment: {error}"
      Keep inputs enabled
      Log to console
```

---

## Comparison: Before vs After

```
BEFORE IMPLEMENTATION
━━━━━━━━━━━━━━━━━━━━━
User enters pack sizes
    ↓
Frontend holds data only
    ↓
Risk: User accidentally changes values
    ↓
Generate labels
    ↓
Need to recalculate for packing slip
    ↓
No audit trail
    ↓
No persistence

AFTER IMPLEMENTATION
━━━━━━━━━━━━━━━━━━━━━━
User enters pack sizes
    ↓
Clicks "🔒 Finalize & Lock"
    ↓
Backend saves to database
    ↓
Frontend disables inputs
    ↓
Safe: Data locked and immutable
    ↓
Generate labels (from finalized data)
    ↓
Generate packing slip (read from DB)
    ↓
Audit trail available
    ↓
Full persistence and tracking
```

---

## Deployment Architecture

```
Production Environment
┌─────────────────────────────────────────────────┐
│                                                 │
│  Frontend (labels-batch.html)                   │
│  ├─ Pallet input field                          │
│  ├─ Finalize button                             │
│  └─ finalizeShipment() function                 │
│                                                 │
│  Backend (FastAPI)                              │
│  ├─ POST /api/labels/finalize/{code}            │
│  ├─ GET /api/packing-slip/{code}                │
│  └─ Existing: /api/labels/generate/             │
│                                                 │
│  Database (SQLite/PostgreSQL)                   │
│  ├─ shipment_boxes table (34 records example)   │
│  ├─ labels table (for future)                   │
│  └─ Indexes: shipment_code, finalized_at        │
│                                                 │
└─────────────────────────────────────────────────┘
```

---

## Performance Considerations

```
Box Calculation (Backend):
  1180 qty ÷ 35 pack_size = 34 boxes
  Time: O(1) - Just division

Database Insert (Backend):
  34 ShipmentBox records
  Time: O(n) where n = box_count
  Typical: < 1 second for 100 boxes

Packing Slip Query (Backend):
  SELECT * FROM shipment_boxes WHERE shipment_code = 'X'
  With index on shipment_code: O(log n)
  Time: < 100ms for 1000s of records

Frontend Rendering:
  Group 1000 boxes by item_code
  Time: O(n) = < 500ms
  Acceptable for user interaction
```

---

This architecture ensures:
✅ Data integrity  
✅ Audit trail  
✅ Performance  
✅ User experience  
✅ Future scalability

# Packing Slip System - Complete Implementation

## Overview
This document describes the complete packing slip system workflow with database integration.

## System Architecture

### Database Tables

#### 1. `shipment_boxes` Table
Stores finalized box configuration for each shipment after user locks in pack sizes.

**Columns:**
- `id` - Primary key
- `shipment_code` - FK to shipment
- `customer_order_code` - Reference to customer order
- `item_code` - Product code
- `item_title` - Product description
- `order_line` - Order line number (for grouping items to combine)
- `pack_size` - Number of items per box
- `box_number` - Box number (1, 2, 3, etc.)
- `quantity_in_box` - Quantity in this specific box
- `total_quantity` - Total quantity for this item
- `lot_codes` - JSON array of lot codes for this product
- `pallet_number` - Optional pallet grouping (for future use)
- `generated_from` - How configuration was created
- `finalized_at` - When configuration was locked

**Key Feature:** Records locked in place - prevents changes after finalization

#### 2. `labels` Table
Stores individual label records with trackable IDs for audit trail.

**Columns:**
- `id` - Primary key
- `label_id` - Unique label identifier (Format: `shipment-PO-item-date`)
  - Example: `SH215601-PO4134724-79300-HPC-20260201`
- `shipment_code` - Reference to shipment
- `customer_order_code` - Reference to order
- `po_number` - PO number for reference
- `item_code` - Product code
- `item_title` - Product description
- `order_line` - Order line for grouping
- `box_number` - Which box
- `quantity` - Quantity in this label
- `pack_size` - Pack size used
- `lot_codes` - JSON array of lot codes
- `label_mode` - 'individual' or 'grouped'
- `generated_at` - When label was generated

---

## User Workflow

### Step 1: View Shipment Details
User expands a shipment to see all products grouped by `item_code + order_line`

**Frontend Display:**
- Items shown **grouped** (same item code + order line combined)
- Shows: Item Code, Description, Total Qty, Qty Remaining, Order Line
- **NEW:** Pallet # field to assign pallet
- Pack Size input for each group
- Label Mode selector (Individual/Grouped)

### Step 2: Set Pack Sizes
User enters desired pack size for each item group
- Example: 79300-HPC order line 1 → pack size 35
- Example: 79300-HPC order line 2 → pack size 25

### Step 3: Assign Pallet (Optional)
User enters pallet number if multiple shipments will be on same pallet
- Example: `PALLET-001`
- Can be same for multiple shipments

### Step 4: Finalize & Lock Configuration
User clicks **"🔒 Finalize & Lock"** button

**What Happens:**
1. Frontend calls `POST /api/labels/finalize/{shipment_code}`
2. Sends: pallet_number + product_configs
3. Backend:
   - Calculates boxes for each product based on pack_size
   - Saves **all box records** to `shipment_boxes` table
   - Returns success with total boxes saved
4. Frontend:
   - Disables all pack size inputs (fields become read-only)
   - Disables pallet input
   - Shows confirmation message

**Important:** After finalization, pack sizes are locked. Data is now in database.

### Step 5: Generate Labels (From Finalized Data)
User clicks printer icon to generate labels for this shipment

**What Happens:**
1. Frontend gets current form values (or from DB if already finalized)
2. Calls `POST /api/labels/generate/{shipment_code}`
3. Backend generates labels based on finalized box data
4. *(Future)* Also saves records to `labels` table with label_id

### Step 6: Generate Packing Slip
User navigates to packing-list.html

**What Happens:**
1. Frontend calls `GET /api/packing-slip/{shipment_code}`
2. Backend queries `shipment_boxes` table
3. Returns all boxes grouped by item_code + order_line
4. Packing slip displays:
   - Item Code, Description, Total Qty, Number of Boxes
   - All box details with box numbers and quantities
   - Pallet number (if assigned)

---

## Backend Endpoints

### 1. GET `/api/labels/shipments/{shipment_code}`
**Purpose:** Get shipment details with enriched product data

**Response:**
```json
{
  "success": true,
  "shipment": {
    "code": "SH215599",
    "customer_name": "Hudson Products",
    "reference": "PO # 4134724",
    "products": [
      {
        "item_code": "79300-HPC",
        "quantity_booked": 619,
        "lot_code": "L00300",
        "order_line": "1",
        "qty_remaining": 1180
      }
    ]
  }
}
```

### 2. POST `/api/labels/finalize/{shipment_code}`
**Purpose:** Lock and save shipment box configuration to database

**Request Body:**
```json
{
  "pallet_number": "PALLET-001",
  "product_configs": {
    "79300-HPC-0": {
      "item_code": "79300-HPC",
      "order_line": "1",
      "pack_size": 35
    }
  }
}
```

**Response:**
```json
{
  "success": true,
  "shipment_code": "SH215599",
  "pallet_number": "PALLET-001",
  "total_boxes_saved": 34,
  "boxes": [...]
}
```

**Database Action:** Saves records to `shipment_boxes` table

### 3. GET `/api/packing-slip/{shipment_code}`
**Purpose:** Get packing slip data from finalized box records

**Response:**
```json
{
  "success": true,
  "shipment_code": "SH215599",
  "items_summary": [
    {
      "item_code": "79300-HPC",
      "item_title": "BOLT HEX NUT...",
      "order_line": "1",
      "pack_size": 35,
      "total_quantity": 1180,
      "total_boxes": 34,
      "pallet_number": "PALLET-001"
    }
  ],
  "all_boxes": [
    {
      "item_code": "79300-HPC",
      "box_number": 1,
      "quantity_in_box": 35,
      "pack_size": 35
    }
  ],
  "total_items": 1,
  "total_boxes": 34
}
```

### 4. POST `/api/labels/generate/{shipment_code}`
**Purpose:** Generate labels from finalized configuration

**Request Body:**
```json
{
  "79300-HPC-0": {
    "item_code": "79300-HPC",
    "order_line": "1",
    "pack_size": 35,
    "label_mode": "individual"
  }
}
```

**Response:**
```json
{
  "success": true,
  "shipment_code": "SH215599",
  "label_mode": "individual",
  "total_labels": 34,
  "labels": [...]
}
```

---

## Frontend Components

### labels-batch.html Updates

#### New Fields:
1. **Pallet # Input**
   - Located in expanded shipment details
   - Optional text field
   - Placeholder: "Optional - for grouping on pallets"
   - Disabled after finalization

2. **Finalize & Lock Button**
   - Icon: 🔒
   - Located in expanded shipment details
   - Calls `finalizeShipment(index)`
   - Shows confirmation after success

#### New Functions:
```javascript
async function finalizeShipment(index) {
  // 1. Collects pack sizes from inputs
  // 2. Collects pallet number
  // 3. Groups products by item_code + order_line
  // 4. Sends POST /api/labels/finalize/
  // 5. Disables inputs on success
  // 6. Shows confirmation message
}
```

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ User Views Shipment in labels-batch.html                       │
│ - Sees products grouped by item_code + order_line              │
│ - Enters pack size, selects pallet #                           │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
         ┌───────────────────────────────┐
         │ Clicks 🔒 Finalize & Lock     │
         │ Button                        │
         └────────────┬──────────────────┘
                      │
         ┌────────────▼──────────────────┐
         │ POST /api/labels/finalize/    │
         │ Sends: pallet_number +        │
         │        product_configs       │
         └────────────┬──────────────────┘
                      │
         ┌────────────▼──────────────────────────┐
         │ Backend:                              │
         │ 1. Calculate boxes per product        │
         │ 2. For each box:                      │
         │    - Create ShipmentBox record        │
         │ 3. Save all to database               │
         │ 4. Return success                     │
         └────────────┬──────────────────────────┘
                      │
         ┌────────────▼──────────────────┐
         │ Frontend:                     │
         │ - Disable pack size inputs    │
         │ - Show success message        │
         │ - Data now locked in DB       │
         └──────────────────────────────┘
                      │
         ┌────────────▼──────────────────────┐
         │ Later: User clicks Generate Labels │
         │ or Views Packing Slip              │
         └────────────┬─────────────────────┘
                      │
    ┌─────────────────┴──────────────────┐
    │                                    │
    ▼                                    ▼
┌──────────────────────┐      ┌─────────────────────┐
│ Generate Labels:     │      │ Packing Slip:       │
│ POST /generate/      │      │ GET /packing-slip/  │
│ Uses finalized data  │      │ Reads from DB       │
│ May save to labels   │      │ Shows boxes & qty   │
│ table later          │      │                     │
└──────────────────────┘      └─────────────────────┘
```

---

## Future Enhancements

### 1. Label ID Generation & Tracking
Currently planned for implementation:
- Generate label_id format: `shipment-PO-item-date`
- Save to `labels` table when labels are generated
- Track all generated labels with audit trail
- Enable reprint functionality

### 2. Pallet Management
Already prepared in schema:
- `pallet_number` field in `shipment_boxes`
- UI field for pallet assignment
- Future: consolidate shipments by pallet
- Track shipments per pallet

### 3. Bulk Operations
- Generate labels for multiple shipments at once
- Assign same pallet to multiple shipments
- Batch packing slip generation

---

## Testing

Run the test script:
```bash
cd c:\mrpeasy\backend-fastapi
python -m venv test
. test/Scripts/Activate.ps1
python test_packing_workflow.py
```

Expected Output:
```
1. Getting ready shipments...
   Found shipment: SH215599

2. Getting shipment details for SH215599...
   Products: 2

3. Building product configs...
   Created configs for 2 products

4. Finalizing shipment with pallet...
   ✓ Finalized! Saved 34 boxes
   Pallet: PALLET-001

5. Getting packing slip data...
   ✓ Retrieved packing slip data
   Total items: 1
   Total boxes: 34
```

---

## Summary

### What We Built:
✅ **Database Tables:** `shipment_boxes` and `labels` for persistent storage  
✅ **Finalize Endpoint:** Lock configuration, save to DB  
✅ **Packing Slip Endpoint:** Read from DB, return grouped data  
✅ **Frontend Pallet Field:** UI for pallet assignment  
✅ **Frontend Lock Button:** Finalize and disable editing  
✅ **Data Integrity:** Once finalized, data cannot be accidentally changed  

### Key Features:
- **Order Line Grouping:** Items automatically grouped by order_line for smart combining
- **Qty Remaining Tracking:** Shows what customer still expects
- **Pallet Support:** Multiple shipments can be grouped on same pallet
- **Audit Trail:** All box data stored for packing slip generation
- **Lock Mechanism:** After finalization, inputs disabled to prevent changes

### Next Steps (Optional):
1. Implement label_id generation and save to labels table
2. Add pallet consolidation logic
3. Build packing slip printing UI (currently in packing-list.html)
4. Add label reprinting from historical labels table

# Database Structure & Invoice/Order Analysis Guide

## 1. DATABASE MODELS STRUCTURE

### Model Definitions Location
- **Primary File**: [backend-fastapi/app/models/__init__.py](../backend-fastapi/app/models/__init__.py) (190 lines)
  - Defines all SQLAlchemy ORM models using declarative base
  - Models are stored in local SQLite database: `backend-fastapi/app/mrpeasy.db`

### Local Database Models
```
User (users table)
  ├─ id, username, email, hashed_password
  ├─ role: owner|admin|editor|viewer
  └─ is_active, created_at, updated_at

Role (roles table)
  ├─ id, name (unique), description
  └─ created_at

CustomerOrder (customer_orders table) ⭐ MAIN DATA
  ├─ id, mrp_cust_ord_id (unique, links to MRPeasy)
  ├─ code (e.g., "C89084", "C89050")
  ├─ customer_id, customer_name, status
  ├─ total_price, currency, delivery_date
  ├─ reference (PO/Reference number)
  └─ synced_at (last update timestamp)

StockItem (stock_items table)
  ├─ id, mrp_article_id (unique)
  ├─ code, title, item_code
  ├─ unit_id, unit, group_id, group_title
  ├─ selling_price, avg_cost
  ├─ in_stock, available, booked, expected_total
  └─ synced_at

ManufacturingOrder (manufacturing_orders table)
  ├─ id, mrp_man_ord_id (unique), code
  ├─ article_id, item_code, item_title
  ├─ quantity, status, due_date, start_date, finish_date
  ├─ item_cost, total_cost
  └─ synced_at

Vendor (vendors table)
  ├─ id, mrp_vendor_id (unique), code, title
  ├─ currency, tax_rate, payment_period, lead_time
  ├─ contact_data (JSON)
  └─ synced_at

Inventory (inventory table)
  ├─ id, article_id, item_code, item_title
  ├─ quantity_on_hand, quantity_available, quantity_booked, quantity_expected
  ├─ unit_cost, total_cost
  └─ snapshot_date

SyncLog (sync_logs table)
  ├─ id, entity_type (customer_orders|stock_items|etc)
  ├─ last_sync, sync_count
  ├─ status (pending|success|failed), error_message
  └─ created_at, updated_at

ShipmentBox (shipment_boxes table)
  ├─ id, shipment_code, customer_order_code
  ├─ po_number, customer_name, shipping_address
  ├─ item_code, item_title, order_line
  ├─ pack_size, box_number, quantity_in_box
  ├─ total_quantity, lot_codes, pallet_number
  ├─ generated_from (individual|grouped)
  └─ finalized_at, created_at, updated_at

Label (labels table)
  ├─ id, label_id (unique)
  ├─ shipment_code, customer_order_code, po_number
  ├─ item_code, item_title, order_line, box_number
  ├─ quantity, pack_size, lot_codes
  ├─ label_mode (individual|grouped)
  └─ generated_at, created_at, updated_at
```

### ⚠️ Critical Note: Invoice Data NOT Stored Locally
**Invoice and OrderLineItem models are NOT in the local database**
- Invoices are fetched directly from MRPeasy API on-demand
- No local caching of invoice data (data freshness issue possible)
- Invoice relationships exist ONLY in MRPeasy's database

---

## 2. API CLIENT & DATA RETRIEVAL

### MRPeasy API Client Location
**File**: [backend-fastapi/app/services/mrpeasy_client.py](../backend-fastapi/app/services/mrpeasy_client.py) (150+ lines)

### Key Methods for Invoice/Order Analysis
```python
# ORDERS
get_customer_orders(filters=None)        # Returns list of all orders
get_customer_order(order_id: int)        # Get single order by ID

# INVOICES (directly from MRPeasy, NOT cached locally)
get_invoices(filters=None)               # Returns list of all invoices
get_invoice(invoice_id: int)             # Get single invoice by ID

# RELATED
get_shipments(filters=None)              # Get all shipments
get_stock_items(filters=None)            # Get all inventory items

# Implementation
_request(method, endpoint, **kwargs)           # Single request
_paginated_request(method, endpoint, **kwargs) # Auto-pagination (1000/batch)
```

### Order Data Structure (from API)
```json
{
  "cust_ord_id": 109,                    // MRPeasy internal ID
  "code": "C89084",                      // Order code (user-facing)
  "reference": "PO-12345",               // Purchase order reference
  "customer_id": 5,
  "customer_name": "Company XYZ",
  "status": 70,                          // Order status code
  "status_txt": "Partially Shipped",     // Human readable
  "invoice_status": 20,                  // 10=not invoiced, 20=partial, 30=full
  "total_price": 1000.00,
  "currency": "USD",
  "delivery_date": "2026-04-15T00:00:00Z",
  "custom_814": "JOB-2025-001",          // Custom fields
  "custom_531": "REF-123",
  
  "products": [                          // Order line items
    {
      "item_code": "76003",
      "item_title": "Widget A",
      "quantity": 100,                   // Total ordered
      "shipped": 80,                     // Already shipped
      "delivery_date": "2026-04-15T00:00:00Z"
    },
    {
      "item_code": "76003",              // SAME ITEM, different delivery_date!
      "item_title": "Widget A",
      "quantity": 50,
      "shipped": 50,
      "delivery_date": "2026-05-01T00:00:00Z"
    }
  ]
}
```

### Invoice Data Structure (from API)
```json
{
  "invoice_id": 50001,                   // MRPeasy internal ID
  "code": "Inv-9601564",                 // Invoice number (user-facing)
  "cust_ord_id": 109,                    // Links to order ID
  "customer_id": 5,
  "type_txt": "Sales Invoice",
  "status": 1,
  "status_txt": "Open",                  // Invoice status
  "total_price": 800.00,
  "total_price_cur": 800.00,
  "currency": "USD",
  "created": "2026-03-20T10:00:00Z",
  "due_date": "2026-04-20T00:00:00Z",
  
  "products": [                          // Invoiced items
    {
      "item_code": "76003",
      "item_title": "Widget A",
      "quantity": 30,                    // Qty on THIS invoice
      "price": 100.00,                   // Unit price
      "line_date": "2026-03-20T00:00:00Z"
    },
    {
      "item_code": "76004",
      "item_title": "Widget B",
      "quantity": 20,
      "price": 150.00,
      "line_date": "2026-03-20T00:00:00Z"
    },
    {
      "item_code": "76003",              // SAME ITEM AGAIN (2nd line)
      "item_title": "Widget A",
      "quantity": 10,                    // Different qty
      "price": 100.00,                   // Same or different price
      "line_date": "2026-03-25T00:00:00Z"
    }
  ]
}
```

---

## 3. ORDER-INVOICE RELATIONSHIPS

### Key Linking Fields
| Field | Use | Location |
|-------|-----|----------|
| `cust_ord_id` | Order ID in MRPeasy | Both Order & Invoice objects |
| `code` | Human-readable code | Order (e.g., "C89084"), Invoice (e.g., "Inv-9601564") |
| `item_code` | Product code | Both Order products[] and Invoice products[] |
| `delivery_date` | Shipment date | Order products[].delivery_date |
| `line_date` | Invoice line date | Invoice products[].line_date |

### Relationship Cardinality
```
CustomerOrder (1) ─── ∞ (Invoices via cust_ord_id)
   │
   └─ products[] (multiple items per order)
        │
        ├─ item_code: "76003"
        ├─ quantity: 100
        ├─ shipped: 80
        └─ delivery_date: "2026-04-15"
        
Invoice (1) ─── ∞ (Products via invoice_id)
   │
   └─ products[] (ONE or MORE lines per item_code)
        │
        ├─ item_code: "76003" (Line 1)
        ├─ quantity: 30
        ├─ price: 100
        └─ line_date: "2026-03-20"
        
        ├─ item_code: "76003" (Line 2 - SAME ITEM!)
        ├─ quantity: 10
        ├─ price: 100
        └─ line_date: "2026-03-25"
```

---

## 4. INVOICE COUNT CALCULATION

### How "4 Invoiced Items" vs "3 Items" Occurs

**Code Location**: [backend-fastapi/app/routes/customer_orders.py](../backend-fastapi/app/routes/customer_orders.py#L55-L85)

```python
# Algorithm:
1. Fetch all invoices from MRPeasy API
2. For each order, group invoices by cust_ord_id
3. For each order product (item_code):
   a. Sum quantities across ALL invoices matching (cust_ord_id + item_code)
   b. Calculate: discrepancy = shipped - invoiced
4. Count method:
   - "Invoiced items" = number of distinct (invoice_id, item_code) pairs
   - NOT distinct item_codes (items can appear on multiple invoices!)
```

### Example Scenario: Order C89084, Item 76003
**Order has 2 line items for "76003"** (different delivery dates):
- Line 1: delivery_date=2026-04-15, quantity=100, shipped=80
- Line 2: delivery_date=2026-05-01, quantity=50, shipped=50

**But invoices show 4 items for this order:**
- Invoice Inv-9601560: [76001 (qty 10), 76002 (qty 15)]
- Invoice Inv-9601564: [76003 (qty 30), 76004 (qty 20), 76003 (qty 10)] ← 2 lines!
- ~~Invoice Inv-9601565~~: (different order)

**Count = 4 because:**
- 76003 appears TWICE in invoices (two separate invoice line products)
- System counts by line_item level, not by unique item_code
- 76001 + 76002 + 76003 (line 1) + 76003 (line 2) = 4 items

**Why 3 items expected:**
- Order expects 3 distinct items: [76001, 76002, 76003]
- But invoice line-item level = 4 lines total

---

## 5. DIAGNOSTIC SCRIPTS FOR ANALYSIS

### Available Data Query Scripts

| Script | Purpose | Location |
|--------|---------|----------|
| **search_invoice.py** | Find specific invoice by code (e.g., "Inv-5464545") | [backend-fastapi/search_invoice.py](../backend-fastapi/search_invoice.py) |
| **search_c89050_invoices_comprehensive.py** | Find ALL invoices for order C89050, totals & discrepancies | [backend-fastapi/search_c89050_invoices_comprehensive.py](../backend-fastapi/search_c89050_invoices_comprehensive.py) |
| **show_c89050_invoices.py** | Show invoices for specific order (C89050) | [backend-fastapi/show_c89050_invoices.py](../backend-fastapi/show_c89050_invoices.py) |
| **check_c89050.py** | Check invoicing status of C89050 | [backend-fastapi/check_c89050.py](../backend-fastapi/check_c89050.py) |
| **show_customer_order.py** | Display full order structure (C89076) | [backend-fastapi/show_customer_order.py](../backend-fastapi/show_customer_order.py) |
| **show_discrepancies.py** | Find ALL orders with under/over-invoiced items | [backend-fastapi/show_discrepancies.py](../backend-fastapi/show_discrepancies.py) |
| **show_categorized_discrepancies.py** | Categorize discrepancies: not_invoiced / under_invoiced / over_invoiced | [backend-fastapi/show_categorized_discrepancies.py](../backend-fastapi/show_categorized_discrepancies.py) |
| **debug_invoicing.py** | Debug invoice status codes (10, 20, etc.) | [backend-fastapi/debug_invoicing.py](../backend-fastapi/debug_invoicing.py) |
| **check_partial_orders.py** | Find orders with partial shipments | [backend-fastapi/check_partial_orders.py](../backend-fastapi/check_partial_orders.py) |
| **check_all_uninvoiced.py** | Find all uninvoiced shipped items | [backend-fastapi/check_all_uninvoiced.py](../backend-fastapi/check_all_uninvoiced.py) |
| **list_shipped_uninvoiced.py** | List shipped but uninvoiced items | [backend-fastapi/list_shipped_uninvoiced.py](../backend-fastapi/list_shipped_uninvoiced.py) |
| **analyze_c89077.py** | Analyze specific order for over-invoicing | [backend-fastapi/analyze_c89077.py](../backend-fastapi/analyze_c89077.py) |
| **final_categorization_test.py** | Comprehensive category test (not/under/over invoiced) | [backend-fastapi/final_categorization_test.py](../backend-fastapi/final_categorization_test.py) |
| **show_shipment_data.py** | Display shipment structure | [backend-fastapi/show_shipment_data.py](../backend-fastapi/show_shipment_data.py) |
| **check_invoice_status.py** | Check invoice_status field values | [backend-fastapi/check_invoice_status.py](../backend-fastapi/check_invoice_status.py) |

### API Routes That Use This Data

| Route | File | Purpose |
|-------|------|---------|
| `/api/customer-orders` | [app/routes/customer_orders.py](../backend-fastapi/app/routes/customer_orders.py) | List orders, attach invoice/shipment data |
| `/api/customer-orders/{order_id}/partial-shipments` | Same | Get orders with partial shipments |
| `/api/invoicing/list` | [app/routes/invoicing.py](../backend-fastapi/app/routes/invoicing.py) | List invoices, generate drafts |
| `/api/invoicing/generate` | Same | Generate invoice from shipments |
| `/api/reports/` | [app/routes/reports.py](../backend-fastapi/app/routes/reports.py) | Report endpoints |

---

## 6. HOW TO INVESTIGATE YOUR SPECIFIC CASES

### Case 1: Invoice Inv-9601564 (should have 3 items)
```bash
cd backend-fastapi
python search_invoice.py
# Find invoice code, display products[], count by line, not by unique item_code
```

### Case 2: Order C89084 (item 76003 as two line items)
```bash
# Method 1: Show full order structure
python show_customer_order.py
# Edit to search for C89084, check products[] array

# Method 2: Show all invoices for this order
python search_c89050_invoices_comprehensive.py
# Edit to use order code 'C89084' instead of 'C89050'

# Method 3: List categorized discrepancies
python show_categorized_discrepancies.py
# See if C89084 appears under over-invoiced or under-invoiced
```

### Case 3: Order shows 4 invoiced items instead of 3
```bash
# This is a COUNTING method issue, not a data issue
# The "4" counts line items (including duplicates)
# The "3" counts unique items

# Root cause locations:
1. app/routes/customer_orders.py (lines 55-85) - invoice aggregation
2. show_discrepancies.py - counting logic
3. Any route using get_invoices() then grouping by item_code

# Fix approach:
- Use DISTINCT count on item_code for each order
- Or aggregate by (cust_ord_id, item_code) not by (invoice_id, item_code)
```

---

## 7. KEY FINDINGS

### Understanding the "4 vs 3" Discrepancy Issue
1. **Order level**: C89084 has 3 expected items
2. **Invoice level**: Invoices contain 4 line items (item 76003 split across 2 lines)
3. **Query issue**: Counting `distinct (invoice_id, item_code)` gives 4
4. **Correct count**: `distinct item_code` should give 3

### Invoice Count Types
| Type | Meaning | Count Method |
|------|---------|--------------|
| **Expected items** | Distinct item_codes in order.products[] | COUNT(DISTINCT item_code) on order |
| **Invoiced line items** | Total products[] rows across all invoices | SUM(invoice_products[] length) |
| **Invoiced items** | Distinct (invoice, item_code) pairs | COUNT(DISTINCT invoice_id, item_code) |
| **Partial invoices** | Items on multiple invoices | Multiple invoices w/ same item_code |

### Common Root Causes of Discrepancies
1. **Same item on multiple invoices**: item 76003 in Inv-9601564, Inv-9601565, etc.
2. **Same item split across lines**: item 76003 appears twice in same invoice (different delivery date splits)
3. **Shipment-based invoicing**: Multiple shipments → Multiple invoices for same item
4. **Partial shipments**: One order item split into 2 shipments with different dates

---

## 8. ROUTES TO EXPLORE INVOICING LOGIC

### Customer Orders Route
**File**: [backend-fastapi/app/routes/customer_orders.py](../backend-fastapi/app/routes/customer_orders.py) (500+ lines)

Key function: Build invoice lookup at lines 55-85
- Creates `invoices_by_order` dict: `{cust_ord_id: [invoice_list]}`
- Called by GET `/api/customer-orders?filter_type=...` endpoints
- Used to attach related invoices to each order response

### Invoicing Route
**File**: [backend-fastapi/app/routes/invoicing.py](../backend-fastapi/app/routes/invoicing.py) (600+ lines)

Key functions:
- `_extract_job_number()`: Custom field extraction
- `GET /api/invoicing/list`: List invoices with filters
- `POST /api/invoicing/generate`: Generate invoice draft from shipments

### Reports Route
**File**: [backend-fastapi/app/routes/reports.py](../backend-fastapi/app/routes/reports.py)

May have invoice summary/aggregation logic

---

## 9. QUICK REFERENCE: HOW TO FIND YOUR DATA

### To find Invoice Inv-9601564:
```
1. Use API: GET /api/invoicing/list?search=Inv-9601564
2. Or script: python search_invoice.py (edit to search for this invoice code)
3. Look for: cust_ord_id, products[], total_price
```

### To find Order C89084's invoices:
```
1. Use API: GET /api/customer-orders?search=C89084
2. Or via DB: SELECT * FROM customer_orders WHERE code='C89084'
3. Get cust_ord_id from result
4. Use script: python search_c89050_invoices_comprehensive.py (edit for C89084)
```

### To understand item 76003 split:
```
1. Get order C89084: order.products[] array
   - Should show 2 lines for 76003 w/ different delivery_dates
2. Get invoices for order: invoices_by_order[cust_ord_id]
   - Check if 76003 appears in multiple invoices
   - Check if 76003 appears multiple times in same invoice
3. Logic: Multiple delivery dates → Multiple invoice lines → "4 vs 3" count
```


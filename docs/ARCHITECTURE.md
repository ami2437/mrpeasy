# Architecture: Data Model, API Client & Data Flow

## MRPeasy integration is read-only

The backend **only reads** from MRPeasy and syncs copies into its local
database; all writes/deletes happen against the local database only. See
[SECURITY.md](SECURITY.md) for the verification checklist.

```
MRPeasy System (production, READ ONLY)
        │  GET requests only
        ▼
FastAPI Backend
  • MRPeasyAPIClient  — only GET methods
  • SyncService       — reads & stores locally
  • CRUD services     — only modify the LOCAL database
        │  local DB operations
        ▼
Local Database (SQLite/PostgreSQL)
  • CustomerOrder / StockItem / ManufacturingOrder / Vendor — synced read-only copies
  • Inventory — historical snapshots
  • SyncLog — tracks data refreshes
  • ShipmentBox / Label — locally-owned packing-slip data (see PACKING_SLIP.md)
        │  JSON responses
        ▼
React / static frontend
```

Allowed vs. blocked operations:

| | Example | Effect |
|---|---|---|
| ✅ Read | `GET /customer-orders`, `GET /stock-items`, `POST /sync/all` | Reads/refreshes local DB from MRPeasy |
| ✅ Local write | `PUT /customer-orders/{id}`, `DELETE /customer-orders/{id}` | Local database only |
| ❌ Blocked | `POST /customer-orders`, any write to MRPeasy | Removed / never implemented |

## Local database models

Defined in `backend-fastapi/app/models/__init__.py`, SQLite file at
`backend-fastapi/mrpeasy.db`.

```
User (users)                  — id, username, email, hashed_password, role, is_active
Role (roles)                  — id, name, description

CustomerOrder (customer_orders) ⭐ main synced data
  id, mrp_cust_ord_id, code (e.g. "C89084"), customer_id, customer_name,
  status, total_price, currency, delivery_date, reference (PO), synced_at

StockItem (stock_items)        — mrp_article_id, code, title, item_code, unit,
                                  group, selling_price, avg_cost, in_stock,
                                  available, booked, expected_total, synced_at

ManufacturingOrder (manufacturing_orders) — mrp_man_ord_id, code, article_id,
                                  item_code, item_title, quantity, status,
                                  due_date, start_date, finish_date, costs

Vendor (vendors)               — mrp_vendor_id, code, title, currency,
                                  tax_rate, payment_period, lead_time,
                                  contact_data (JSON)

Inventory (inventory)          — article_id, item_code, item_title,
                                  quantity_on_hand/available/booked/expected,
                                  unit_cost, total_cost, snapshot_date

SyncLog (sync_logs)            — entity_type, last_sync, sync_count, status

ShipmentBox (shipment_boxes)   — see PACKING_SLIP.md
Label (labels)                 — see PACKING_SLIP.md
```

**Invoices are not stored locally.** Invoice data is fetched directly from
the MRPeasy API on demand (no local caching), so there is no `Invoice` model.

## MRPeasy API client

`backend-fastapi/app/services/mrpeasy_client.py`. Key read methods:

```python
get_customer_orders(filters=None) / get_customer_order(order_id)
get_invoices(filters=None) / get_invoice(invoice_id)   # not cached locally
get_shipments(filters=None)
get_stock_items(filters=None)
_request(method, endpoint, **kwargs)
_paginated_request(method, endpoint, **kwargs)          # auto-pagination, 1000/batch
```

### Order shape (from MRPeasy API)
```json
{
  "cust_ord_id": 109,
  "code": "C89084",
  "reference": "PO-12345",
  "status_txt": "Partially Shipped",
  "invoice_status": 20,
  "products": [
    { "item_code": "76003", "quantity": 100, "shipped": 80, "delivery_date": "2026-04-15T00:00:00Z" },
    { "item_code": "76003", "quantity": 50, "shipped": 50, "delivery_date": "2026-05-01T00:00:00Z" }
  ]
}
```
Note: the same `item_code` can appear on multiple order lines with different
`delivery_date`s — each is a distinct shipment/line, not a duplicate.

### Invoice shape (from MRPeasy API)
```json
{
  "invoice_id": 50001,
  "code": "Inv-9601564",
  "cust_ord_id": 109,
  "products": [
    { "item_code": "76003", "quantity": 30, "price": 100.00, "line_date": "2026-03-20T00:00:00Z" },
    { "item_code": "76003", "quantity": 10, "price": 100.00, "line_date": "2026-03-25T00:00:00Z" }
  ]
}
```
Same `item_code` can also appear on multiple invoice lines within one
invoice.

### Order ↔ invoice linking

| Field | Use |
|---|---|
| `cust_ord_id` | Links Order ⇄ Invoice |
| `code` | Human-readable order/invoice number |
| `item_code` | Product code, present on both sides |
| `delivery_date` / `line_date` | Per-line dates used to disambiguate repeated `item_code`s |

### "Invoiced items" counting

Invoicing logic (`backend-fastapi/app/routes/customer_orders.py`) aggregates
by `(invoice_id, item_code)` line pairs, not by distinct `item_code`. A single
`item_code` split across multiple invoice lines (different `line_date`s)
counts once per line — this is expected MRPeasy behavior, not a bug, and
explains apparent mismatches like "4 invoiced lines" vs "3 distinct items".
Discrepancy detection = `shipped_qty - invoiced_qty`, aggregated per
`item_code` across all matching invoices for the order.

## Diagnostic/investigation scripts

`backend-fastapi/` contains one-off scripts used to inspect specific orders,
invoices, and shipments (e.g. `search_invoice.py`, `check_c89050.py`,
`show_customer_order.py`, `show_shipment_data.py`). These are ad-hoc
debugging tools, not part of the application; keep or delete individually as
needed — they aren't covered by this documentation.

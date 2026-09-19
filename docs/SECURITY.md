# Security: MRPeasy Read-Only Guarantee

**Status**: Verified — all MRPeasy data protection measures in place.

The application never sends, modifies, or deletes data in MRPeasy. It only
reads from the MRPeasy API and stores/mutates data in its own local database.

## Verification checklist

### MRPeasy API client (`app/services/mrpeasy_client.py`)
Only GET methods exist: `get_customer_orders`, `get_customer_order`,
`get_stock_items`, `get_stock_item`, `get_manufacturing_orders`,
`get_manufacturing_order`, `get_vendors`, `get_inventory`, `get_report`.
No POST/PUT/DELETE methods. `create_customer_order` / `update_customer_order`
were removed.

### Customer orders routes (`app/routes/customer_orders.py`)
- `GET /` and `GET /{order_id}` read from the local database only.
- `PUT /{order_id}` and `DELETE /{order_id}` mutate the **local** database
  only (docstrings explicitly state they do not touch MRPeasy).
- `POST /` (which would have written to MRPeasy) was removed.

### Sync routes (`app/routes/sync.py`)
One-way sync only: `POST /sync/customer-orders`, `/sync/stock-items`,
`/sync/manufacturing-orders`, `/sync/all` — each docstring states "READ-ONLY,
only fetches data from MRPeasy, never sends or modifies anything".

### Main application (`app/main.py`)
`GET /health` reports:
```json
{ "status": "OK", "mode": "read-only", "mrpeasy_protection": "ENABLED - No write requests to MRPeasy" }
```
`GET /` reports:
```json
{ "mode": "READ-ONLY", "safety": "This API never sends, modifies, or deletes data in MRPeasy" }
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full data-flow diagram.

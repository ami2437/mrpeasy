# COMPREHENSIVE BUG ANALYSIS: Invoice Inv-9601564 / Order C89084

## Executive Summary
**Root Cause**: The invoice Inv-9601564 is missing the line item for **76003-NUT** even though 500 units were shipped. This is a data quality issue where item 76003-NUT was shipped but excluded from the invoice creation.

**Impact**: Order C89084 shows 4 items were shipped (76003, 76003-NUT, 32169, 79299-HPC from both old and new shipments), but only 3 are in the invoice. This creates an under-invoiced discrepancy.

---

## Detailed Analysis

### Data State
**Invoice Inv-9601564:**
- Line 1: 32169, qty=250
- Line 2: 79299-HPC, qty=2400
- Line 3: 76003, qty=500
- Line 4: Shipping, qty=1

**Order C89084 (6 line items):**
```
Line 1: 76003,     qty=500, shipped=0   (future delivery: 2026-04-14)
Line 2: 76003-NUT, qty=500, shipped=0   (future delivery: 2026-04-14)
Line 3: 32169,     qty=250, shipped=250 (past delivery: 2026-03-20)
Line 4: 79299-HPC, qty=2400, shipped=2400 (past delivery: 2026-03-20)
Line 5: 76003,     qty=500, shipped=500 (past delivery: 2026-03-20)
Line 6: 76003-NUT, qty=500, shipped=500 (past delivery: 2026-03-20)
```

### Items Shipped (Should Be Invoiced)
- 32169: 250 units ✓ (in invoice)
- 79299-HPC: 2400 units ✓ (in invoice)
- 76003: 500 units ✓ (in invoice)
- **76003-NUT: 500 units ✗ (MISSING from invoice)**

### System Behavior
The backend invoicing detection logic in `app/routes/invoicing.py` correctly:
1. **Aggregates** order items by `item_code` (handles multiple lines with same item)
2. **Aggregates** invoice items by `item_code`
3. **Calculates** discrepancy: `shipped_qty - invoiced_qty`

For this order, the system would correctly detect:
- 76003: shipped 500 - invoiced 500 = **0 discrepancy** (OK)
- 76003-NUT: shipped 500 - invoiced 0 = **500 under-invoiced** (PROBLEM!)

### Why "4 items as invoiced" Shows Up
The discrepancy reporting shows 4 **shipped line items** in the order (across two delivery dates):
- 32169 (shipped 250 on 2026-03-20)
- 79299-HPC (shipped 2400 on 2026-03-20)
- 76003 (shipped 500 on 2026-03-20)
- 76003-NUT (shipped 500 on 2026-03-20)

But only 3 of them appear in the invoice (32169, 79299-HPC, 76003).

---

## Root Cause

The bug is in the **invoice generation logic** (likely in MRPeasy API or how we're syncing/creating invoices). Two possibilities:

### Scenario 1: MRPeasy API Issue
When creating invoice Inv-9601564 in MRPeasy, the system:
1. Correctly included items 32169, 79299-HPC, and one 76003 line
2. **Failed to include** the 76003-NUT line item (possibly due to how MRPeasy links supplementary items)

### Scenario 2: Local Data Sync Issue
When fetching the invoice from MRPeasy API via `get_invoices()`:
1. The API returns the correct invoice
2. But a parsing/filtering step is removing 76003-NUT somewhere in our code

---

## Potential Causes to Investigate

1. **Item Linking**: Is 76003-NUT a supplementary/related item to 76003? If so:
   - MRPeasy might not automatically include related items in invoices
   - It might require explicit selection/configuration

2. **Delivery Date Handling**: The shipped items span two delivery dates:
   - Future delivery (2026-04-14): 76003, 76003-NUT
   - Past delivery (2026-03-20): 32169, 79299-HPC, 76003, 76003-NUT
   - MRPeasy might be creating a partial invoice for only one delivery date

3. **Quantity Handling**: Both 76003 items have qty=500:
   - MRPeasy might be treating them as duplicates and consolidating?
   - Or silently dropping one due to a logic error?

---

## How to Fix

### Short Term (Workaround)
1. Manually verify invoice Inv-9601564 in MRPeasy
2. If 76003-NUT is truly missing, **update the invoice** to include it (or recreate it)
3. Verify the corrected invoice syncs back to our system

### Medium Term (Data Correction)
1. Run diagnostic query to find all orders where:
   - 76003 and 76003-NUT were shipped together
   - But invoice only includes one or the other
   - Pattern: Item paired across multiple delivery dates

2. For affected invoices, either:
   - Update in MRPeasy and resync
   - Or create supplementary invoices to cover missing items

### Long Term (Prevent Recurrence)

**Option A: Add Validation to Invoice Loading**
Location: `app/services/mrpeasy_client.py` → `get_invoices()` method

```python
def get_invoices(self, filters: Optional[Dict] = None) -> list:
    """Get all sales invoices with automatic pagination"""
    invoices = self._paginated_request("GET", "/invoices", params=filters or {})
    
    # NEW: Validate invoices have all shipped items
    for invoice in invoices:
        self._validate_invoice_completeness(invoice)
    
    return invoices

def _validate_invoice_completeness(self, invoice):
    """Flag invoices where shipped items don't match invoice items"""
    cust_ord_id = invoice.get('cust_ord_id')
    invoice_code = invoice.get('code')
    
    if not cust_ord_id:
        return
    
    # Get the order
    orders = self._paginated_request("GET", "/customer_orders")
    order = next((o for o in orders if o.get('cust_ord_id') == cust_ord_id), None)
    
    if not order:
        return
    
    # Aggregate shipped items in order
    shipped_items = {}
    for prod in order.get('products', []):
        if prod.get('shipped', 0) > 0:
            code = prod.get('item_code')
            shipped_items[code] = shipped_items.get(code, 0) + prod.get('shipped', 0)
    
    # Aggregate invoiced items in invoice
    invoiced_items = {}
    for prod in invoice.get('products', []):
        if prod.get('item_code', '').lower() != 'shipping':
            code = prod.get('item_code')
            invoiced_items[code] = invoiced_items.get(code, 0) + prod.get('quantity', 0)
    
    # Check for missing items
    missing = set(shipped_items.keys()) - set(invoiced_items.keys())
    
    if missing:
        print(f"WARNING: Invoice {invoice_code} (C.O. {cust_ord_id}) missing items: {missing}")
        # Could log this for later analysis
```

**Option B: Monitor Invoicing Discrepancies**
Location: Daily sync job or scheduled task

```python
# After each invoice sync, run discrepancy detection
from app.routes.invoicing import get_shipped_uninvoiced_items

result = get_shipped_uninvoiced_items()

# Alert if discrepancies exist
if result.get('total_uninvoiced_items', 0) > 0:
    send_alert(f"Found {result.get('total_uninvoiced_items')} uninvoiced items")
    # Log details for analysis
```

**Option C: Improve Invoice Display in UI**
Location: `frontend/public/invoicing.html`

Add a **"Missing Items Alert"** row that shows:
- Order code, invoice code
- Items that were shipped but not in invoice
- Action button to re-sync or create supplementary invoice

---

## Verification Steps

1. **Check MRPeasy directly**: Does Inv-9601564 in MRPeasy contain 76003-NUT?
   - If YES → Our system is filtering it out incorrectly
   - If NO → MRPeasy created the invoice incomplete

2. **Query the API directly**:
```python
from app.services.mrpeasy_client import mrpeasy_client

inv = mrpeasy_client.get_invoice(174)  # Invoice ID
print(inv.get('products'))  # Should show all line items
```

3. **Re-fetch from API** to clear any local caching:
```python
# Clear cache and re-fetch
mrpeasy_client.get_invoices()  # This fetches fresh from API
```

4. **Check if this pattern repeats**: Query for other invoices where items shipped but not invoiced:
```sql
-- Conceptually, find orders where:
-- - Order has item 76003-NUT shipped
-- - But invoice doesn't include 76003-NUT
-- - But does include other items
```

---

## Files to Check/Update

- `app/services/mrpeasy_client.py` - Invoice fetching logic
- `app/routes/invoicing.py` - Discrepancy detection (appears correct)
- `app/routes/customer_orders.py` - Order detail retrieval
- `frontend/public/invoicing.html` - UI display (for adding missing item alerts)

---

## Recommended Action Plan

1. **Immediate**: Verify if 76003-NUT is in MRPeasy's Inv-9601564
2. **Short-term**: If missing, recreate or update the invoice in MRPeasy
3. **Medium-term**: Add code validation to flag this pattern
4. **Long-term**: Implement automated alerts for invoice/shipment mismatches

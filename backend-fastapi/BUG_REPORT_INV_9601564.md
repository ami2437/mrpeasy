# BUG REPORT: Invoice Inv-9601564 & Order C89084

## Issue Summary
The order window shows **4 items as invoiced** when only **3 product items** were invoiced. Invoice Inv-9601564 is missing the **76003-NUT** item.

## Details

### What the Invoice shows (Inv-9601564):
```
1. 32169: qty=250
2. 79299-HPC: qty=2400
3. 76003: qty=500
4. Shipping: qty=1
```
**Total: 3 product items** (not counting Shipping)

### What the Order shows (C89084):
The order has 6 line items:
```
1. 76003: qty=500, shipped=0 (future delivery)
2. 76003-NUT: qty=500, shipped=0 (future delivery)
3. 32169: qty=250, shipped=250 ✓ (in invoice)
4. 79299-HPC: qty=2400, shipped=2400 ✓ (in invoice)
5. 76003: qty=500, shipped=500 ✓ (in invoice)
6. 76003-NUT: qty=500, shipped=500 ✗ (MISSING from invoice!)
```

### The Bug:
Item **76003-NUT** was shipped (500 units) but is **NOT included in the invoice**!

**Expected invoice items (4):**
1. 32169 (250 shipped)
2. 79299-HPC (2400 shipped)
3. 76003 (500 shipped)
4. 76003-NUT (500 shipped) ← **MISSING**

**Actual invoice items (3):**
1. 32169
2. 79299-HPC
3. 76003
(Missing 76003-NUT)

## Root Cause Analysis

The problem occurs in the invoice generation logic. When creating the invoice:

1. **Item 76003** appears as TWO separate line items in the order (with different delivery dates)
   - One shipped (500 units on 1773633599)
   - One not yet shipped (500 units on 1776484799)

2. **Item 76003-NUT** appears as TWO separate line items in the order (with same corresponding dates)
   - One shipped (500 units on 1773633599)
   - One not yet shipped (500 units on 1776484799)

3. When the invoice was generated, it **only included the shipped instances of 76003** but **completely omitted 76003-NUT** even though it was also shipped.

## Solution Options

1. **Ensure all shipped items are included in invoices**, regardless of whether they have companion items with different delivery dates.

2. **Check the invoice generation logic** - likely in `app/services/` or API client - to make sure:
   - All line items with `shipped > 0` are included in the invoice
   - No items are skipped if they have the same item code with different delivery dates

3. **Verify MRPeasy API behavior** - The invoice might be created in MRPeasy and then fetched here. If so, the issue might be on the MRPeasy side where it's not including the 76003-NUT line item when creating the invoice.

## Files to Check

- `app/services/mrpeasy_client.py` - API client that fetches invoices
- `app/` - Any invoice generation/sync logic
- MRPeasy API directly for the root cause

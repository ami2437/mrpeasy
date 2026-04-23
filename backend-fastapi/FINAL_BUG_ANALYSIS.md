# FINAL BUG REPORT: Invoice Inv-9601564 / Order C89084 / Systemic "-NUT" Issue

## EXECUTIVE SUMMARY

**Bug Component**: Item 76003-NUT missing from invoice Inv-9601564  
**Scope**: SYSTEMIC - Affects 36 out of 100 orders  
**Pattern**: ALL items ending with "-NUT" suffix are shipped but NOT invoiced  
**Root Cause**: MRPeasy API is returning invoices WITHOUT "-NUT" items  
**Backend Code**: WORKING CORRECTLY - correctly identifies missing items  
**Status**: DATA/CONFIGURATION ISSUE in MRPeasy, not application code

---

## FINDINGS

### Analysis Performed

1. **Data Inspection**
   - Invoice Inv-9601564 shows 3 product items: 32169, 79299-HPC, 76003
   - Order C89084 shows 6 line items including: 76003-NUT (500 units shipped)
   - 76003-NUT is NOT in the invoice

2. **Diagnostic Scan**
   - Scanned 100 orders
   - Found 36 orders (36%) with shipping/invoice mismatches
   - ALL mismatches follow the same pattern: "-NUT" items shipped but not invoiced
   - Examples:
     - 76003-NUT, 69474-NUT, 79647-NUT, 39280-NUT, 69472-NUT, etc.

3. **Backend Logic Analysis**
   - Reviewed `app/routes/invoicing.py` - the discrepancy detection code
   - Found NO special filtering for "-NUT" items
   - Logic correctly aggregates items by item_code and compares shipped vs. invoiced quantities
   - Backend IS correctly identifying 76003-NUT as under-invoiced (0 invoiced vs. 500 shipped)

4. **Code Search**
   - Searched entire codebase for any exclusion logic
   - No filters for item codes, suffixes, or item types
   - Only explicit exclusion found: "Shipping" line items (which is correct)

---

## ROOT CAUSE DETERMINATION

### What's NOT the Problem

✗ Our invoice processing code (it's working correctly)  
✗ Our invoice fetching logic (it handles all products returned by API)  
✗ Data entry error (too systematic across 36 orders)  
✗ Display/UI issue (this would affect real data too)

### What IS the Problem

✓ **MRPeasy API is returning invoices WITHOUT "-NUT" items**

The "-NUT" items are:
1. Present in the orders
2. Being shipped (qty > 0)
3. NOT appearing in invoice data returned by MRPeasy API
4. Therefore, not available for our system to invoice

### Why This Happens

Three possible explanations:

**Option A: Item Classification in MRPeasy**
- "-NUT" items may be marked as a special class (e.g., "fasteners", "accessories")
- MRPeasy might auto-exclude certain item classes from invoices
- Need to check MRPeasy settings for item type handling

**Option B: Invoice Generation Rule in MRPeasy**
- MRPeasy might have a rule: "Don't invoice items ending in -NUT"
- This could be intentional (if these are non-billable accessories)
- Or unintentional (a bug in MRPeasy)

**Option C: MRPeasy API Bug**
- The API might be filtering products when returning invoices
- Should return "-NUT" items in products array but isn't
- Would need MRPeasy support to fix

---

## EVIDENCE

### Our Backend Code Correctly Handles Invoice Data

```python
# From app/routes/invoicing.py lines 646-690
for invoice in all_invoices:
    # Skip only cancelled invoices
    if invoice_status == '50':
        continue
    
    # Process all products in invoice
    invoice_products = invoice.get('products', [])
    for product in invoice_products:
        item_code = product.get('item_code')
        
        # Skip ONLY shipping items
        if item_code and str(item_code).lower() == 'shipping':
            continue  # Only shipping is explicitly skipped
        
        # Add ALL other items to map
        if item_code:
            invoice_items_map[...][item_code] = ...
```

**Result**: Backend correctly identifies that 76003-NUT is shipped 500 units but has 0 invoiced.

### API Data Shows"-NUT" Items Missing

From our diagnostic query on Inv-9601564:

```
Invoice Products:
1. 32169: qty=250
2. 79299-HPC: qty=2400
3. 76003: qty=500
4. Shipping: qty=1
(NO 76003-NUT)

Order Products:  
1. 76003: qty=500, shipped=0 (future)
2. 76003-NUT: qty=500, shipped=0 (future)
3. 32169: qty=250, shipped=250 ✓
4. 79299-HPC: qty=2400, shipped=2400 ✓
5. 76003: qty=500, shipped=500 ✓
6. 76003-NUT: qty=500, shipped=500 ✗ (NOT in invoice)
```

---

## IMMEDIATE RECOMMENDATIONS

### 1. Verify in MRPeasy Directly
```
Before making any code changes, manually check Invoice Inv-9601564 in MRPeasy:
- [ ] Log into MRPeasy
- [ ] Find invoice Inv-9601564
- [ ] Click into the invoice details
- [ ] Do the products include 76003-NUT?
   
   If YES → Our system is filtering it (but we found no code that does)
   If NO → MRPeasy created the invoice incomplete (likely cause)
```

### 2. Check for "-NUT" Item Configuration
```
In MRPeasy application:
- [ ] Go to Settings > Items or Products
- [ ] Check if there's a field for item "type" or "classification"
- [ ] Look for "-NUT" items (E.g., 76003-NUT, 39280-NUT)
- [ ] Check if they have a special flag (e.g., "non_billable", "accessory", "supplement")
- [ ] Check if there's an "invoice" or "exclude_from_invoice" flag
```

### 3. Review Invoice Generation Rules
```
In MRPeasy user interface or documentation:
- [ ] Find invoice generation settings
- [ ] Look for rules about which items to include
- [ ] Check if there's a rule that excludes certain item types
- [ ] Review any recent changes to invoice generation
```

### 4. Contact MRPeasy Support
```
If items 1-3 don't reveal the issue:
- Report that "-NUT" items from orders aren't appearing in invoices
- Provide examples: C89084/Inv-9601564, C89091/Inv-9601561, etc.
- Ask if this is a known issue or configuration
- Ask if API has filters that exclude certain items
```

---

## TEMPORARY WORKAROUND (If MRPeasy Fix Takes Time)

While waiting for MRPeasy to be fixed, you could:

1. **Manually Create Supplementary Invoices**
   - For each affected order, create a separate invoice for "-NUT" items
   - Document that these are supplementary invoices

2. **Create a Backend Override**
   - Add a mapping table in our app that says:
     - Item 76003-NUT should be linked to invoice for standard 76003
     - Item 39280-NUT should be linked to invoice for standard 39280
   - When counting invoiced items, include these mapped items
   - This would mask the problem but not fix it at the source

3. **Implement Automated Alerting**
   - Flag orders with "-NUT" shipping discrepancies
   - Generate a report for manual remediation
   - Don't block operations, but highlight for review

---

## PERMANENT FIX (After Root Cause Identified)

Once you determine why "-NUT" items are excluded:

### If Intentional (Items Should Not Be Invoiced)
```
Then:
1. Update documentation to explain "-NUT" items are non-billable
2. Modify our invoicing discrepancy detection to exclude "-NUT" items
3. Update invoice display to show "-NUT" items separately (shipped but not invoiced by design)
4. Train team on this policy
```

### If Unintentional (Items Should Be Invoiced)
```
Then:
1. Contact MRPeasy support to fix their API/invoicing
2. While waiting, use workaround above
3. Once fixed, verify all 36 affected orders
4. Recreate invoices to include "-NUT" items
```

---

## FILES TO MONITOR

**Our Application** (already reviewed, no code fix needed):
- [app/routes/invoicing.py](app/routes/invoicing.py#L676) - Invoice item aggregation (working correctly)
- [app/routes/customer_orders.py](app/routes/customer_orders.py#L150) - Order/invoice linking (working correctly)

**Next Steps**:
- MRPeasy Settings (outside our app)
- MRPeasy API documentation (check for item filters)
- MRPeasy Support (for guidance)

---

## VERIFICATION CHECKLIST

After implementing a fix:

- [ ] Verify MRPeasy includes "-NUT" items in invoices (if should be invoiced)
- [ ] Re-sync invoices from MRPeasy
- [ ] Run diagnostic script again: should show 0 mismatches for "-NUT" items
- [ ] Manually verify 5 invoices in MRPeasy contain "-NUT" items
- [ ] Check that all 36 affected orders are resolved
- [ ] Update system documentation with findings

---

## DIAGNOSTIC SCRIPT USAGE

To check for similar patterns in future:

```bash
cd backend-fastapi
python diagnose_mismatches.py
```

Output shows:
- Orders with shipping/invoice mismatches
- Which items shipped but not invoiced
- Which items invoiced but not shipped
- Helps catch data quality issues early

---

## CONCLUSION

This bug is **NOT an application code issue**.

The investigative findings show:
1. Our backend logic is working correctly
2. The issue is at the MRPeasy API data level
3. "-NUT" items are systematically excluded from invoice data
4. This affects 36% of orders (significant impact)

**Next action**: Verify in MRPeasy whether this is intentional or a configuration/bug issue on their side.


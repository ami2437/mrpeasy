# CRITICAL BUG REPORT: Systematic Pattern of "-NUT" Items Missing from Invoices

## EXECUTIVE SUMMARY

**SCOPE**: THIS IS A SYSTEMIC ISSUE, NOT ISOLATED TO ONE ORDER

- **36 orders out of 100** have shipping/invoice mismatches
- **Pattern**: Items with "-NUT" suffix in item code are being shipped but NOT invoiced
- **Example**: Order C89084 (Invoice Inv-9601564) missing 76003-NUT
- **Total Impact**: Hundreds of units shipped but unaccounted for in invoicing

---

## DIAGNOSTIC RESULTS

### Orders Affected (Sample)

1. **C89084** - Hudson Products
   - Invoice: Inv-9601564
   - **MISSING: 76003-NUT (500 units)**

2. **C89091** - Hudson Products
   - Invoice: Inv-9601561
   - MISSING: 69474-NUT (580 units), 69473-NUT (1060 units)

3. **C89086** - Hudson Products
   - Invoice: Inv-9601532
   - MISSING: 79647-NUT (24 units), 30745-NUT (24 units)

4. **C89085** - Hudson Products
   - Invoice: Inv-9601533
   - MISSING: 39222-NUT (216), 79647-NUT (432), 39221-NUT (24), 38544-NUT (60), 69472-NUT (108)

5. **C89081** - Hudson Products
   - Invoice: Inv-9601562
   - MISSING: 39280-NUT (5762 units)

6. **C89073** - Hudson Products
   - Invoices: Inv-9601538, Inv-9601537, Inv-9601525
   - MISSING: Multiple "-NUT" items (77029 - Nuts, 69474-NUT, 69638-NUT, etc.)

And 30 more orders with similar patterns...

---

## PATTERN ANALYSIS

### What We Know

1. **Item Pattern**: ALL missing items end with "-NUT"
   - Examples: 76003-NUT, 39280-NUT, 79647-NUT, 69472-NUT, etc.
   - These are NOT random individual cases

2. **Quantity Pattern**: When shipped, the "-NUT" items are shipped in quantities matching their paired base items
   - Example: 76003 shipped with 76003-NUT (both 500 units)
   - Example: 39280 shipped with 39280-NUT (both 5762 units)

3. **Invoice Pattern**: 
   - Base items (76003, 39280, etc.) ARE in invoices
   - "-NUT" items are COMPLETELY ABSENT from invoices
   - This suggests the invoice generation logic is systematically filtering them out

4. **System Wide**: 36% of orders affected (36/100)

---

## LIKELY ROOT CAUSE

The "-NUT" items are **supplementary/paired items** that:

1. **Are created as separate SKUs/line items** in orders
2. **Ship together** with the base item
3. **Are excluded** by the invoice generation logic

This could be due to:
- A toggle/config flag that says "don't invoice -NUT items"
- Logic that treats "-NUT" suffix specially (thinking they're nuts/fasteners, not billable)
- A filter in MRPeasy API or in our fetching code

### Possible Implementation Location

The exclusion could be in:
1. **MRPeasy's invoice generation** - excludes "-NUT" items by default
2. **Our invoice fetching code** - filtering out products with "-NUT" in the name
3. **Order processing logic** - when creating invoices, skipping certain items

---

## IMPACT ASSESSMENT

### Financial Impact
- Revenue not captured for hundreds of units
- Invoicing discrepancies create accounting problems
- Customer billing incomplete

### Operational Impact
- Discrepancy reports show "under-invoiced" items
- Manual audits required to identify and correct
- Potential customer disputes

### Data Quality Impact
- 36% of orders have inconsistent shipping/invoicing data
- Reports and dashboards showing incorrect summaries

---

## IMMEDIATE ACTION ITEMS

### 1. Verify MRPeasy Configuration
Check if MRPeasy has a setting to exclude certain item types:
- [ ] Look for item classification or "type" filters
- [ ] Check if "-NUT" items are marked as "not billable"
- [ ] Review invoice generation rules

### 2. Check Code for "-NUT" Exclusions
Search codebase for:
```
"-NUT"
suffix
"NUT"
filter (related to items)
```

Specifically check:
- `app/routes/invoicing.py` - discrepancy detection logic
- `app/services/mrpeasy_client.py` - invoice fetching
- `app/routes/customer_orders.py` - order product processing

### 3. Audit Recent Invoices
- [ ] Manually check 5 invoices in MRPeasy (Inv-9601564, Inv-9601561, etc.)
- [ ] Do they contain "-NUT" items or not?
- [ ] If YES → Our system is filtering them out
- [ ] If NO → MRPeasy is excluding them

### 4. Decision on Correction
- [ ] Should "-NUT" items be invoiced? (Probably YES if shipped)
- [ ] Are they billable? (Verify pricing)
- [ ] Create plan to retroactively invoice 36 affected orders

---

## INVESTIGATION CHECKLIST

- [ ] Find where "-NUT" items are excluded
- [ ] Determine if this is intentional or a bug
- [ ] Check MRPeasy documentation for item types/classes
- [ ] Look for any comments in code mentioning "NUT" or "supplementary"
- [ ] Check git history for when this logic was added
- [ ] Review order processing tests for "-NUT" item handling
- [ ] Verify all 36 affected orders (not just sample)

---

## FILES TO INVESTIGATE

1. **app/routes/invoicing.py** - Line ~676 (where Shipping is skipped)
   - Is there similar logic skipping "-NUT" items?

2. **app/services/mrpeasy_client.py** - Invoice fetching
   - Any product filtering?

3. **app/routes/customer_orders.py** - Order processing
   - Any product filtering?

4. **Database models** - app/models/__init__.py
   - Any item type/class fields?

5. **Frontend** - frontend/public/invoicing.html or dashboard.html
   - Any display filters for "-NUT" items?

---

## NEXT STEPS

1. **URGENT**: Find the code/config causing "-NUT" exclusion
2. **SHORT-TERM**: Manually correct affected invoices in MRPeasy
3. **MEDIUM-TERM**: Fix the underlying logic
4. **LONG-TERM**: Implement validation to prevent future mismatches
5. **DOCUMENTATION**: Document why "-NUT" items exist and how they should be invoiced

---

## SUMMARY

**This is NOT a data entry error or one-off bug.**

This is a **systematic design or configuration issue** where:
- One specific product category ("-NUT" items) is being handled differently
- The logic appears intentional (too consistent to be random)
- But results in serious invoicing discrepancies

**The fix requires understanding WHY "-NUT" items are excluded and deciding if that's correct.**

# Understanding the Order C89084 Structure

## How The Order Is Organized

Order C89084 has 6 line items, organized by **delivery date**:

### SHIPMENT 1: Future Delivery (April 14, 2026)
```
Line 1: 76003      qty=500, units shipped=0 (not delivered yet)
Line 2: 76003-NUT  qty=500, units shipped=0 (not delivered yet)
```
Status: **PENDING** - not yet shipped

### SHIPMENT 2: Past Delivery (March 20, 2026)  
```
Line 3: 32169      qty=250, units shipped=250 (fully delivered)
Line 4: 79299-HPC  qty=2400, units shipped=2400 (fully delivered)
Line 5: 76003      qty=500, units shipped=500 (fully delivered)  
Line 6: 76003-NUT  qty=500, units shipped=500 (fully delivered)
```
Status: **DELIVERED** - already shipped

---

## Why Two Shipments?

This is a common scenario:
1. Customer ordered multiple items with DIFFERENT delivery dates
2. Some items ready to ship now (March 20)
3. Some items ready later (April 14)
4. MRPeasy creates separate order lines for each delivery date

---

## The Bundled Items Problem

Notice that:
- **76003 and 76003-NUT are a paired set** (base item + accessory)
- When 76003 shipped (500 units), 76003-NUT shipped too (500 units)
- Both have same qty across both shipments (500 each)

This is a **"bundled item" or "kit" pattern**:
- 76003 is the main product
- 76003-NUT is the nuts/fastener accessory that goes with it
- They ship together as a set

---

## What Gets Invoiced

**Expected** (what SHOULD be invoiced):
```
From Shipment 2 (March 20 delivery):
- 32169: qty 250 ✓
- 79299-HPC: qty 2400 ✓
- 76003: qty 500 ✓
- 76003-NUT: qty 500 ✓ (should be here but ISN'T)
```

**Actual** (what's in Inv-9601564):
```
- 32169: qty 250 ✓
- 79299-HPC: qty 2400 ✓
- 76003: qty 500 ✓
- 76003-NUT: ✗ MISSING
```

---

## Why This Matters

1. **Item Count Confusion**
   - Unique items in order: 4 (76003, 76003-NUT, 32169, 79299-HPC)
   - Line items in shipment: 4 (split by delivery date)
   - Items in invoice: 3 (missing 76003-NUT)
   - User sees: "4 items as invoiced" but invoice shows only 3 products

2. **Bundled Item Risk**
   - If a base item ships, its accessories usually ship too
   - If only the base item is invoiced, the accessory "disappears" from billing
   - Revenue not captured for accessory items

3. **Under-Invoiced Report**
   - 76003: shipped 500, invoiced 500 = **OK**
   - 76003-NUT: shipped 500, invoiced 0 = **UNDER-INVOICED!**

---

## This Pattern Across All 36 Affected Orders

The same thing happens in every affected order:

| Item | Qty Shipped | Qty Invoiced | Status |
|------|------------|-------------|--------|
| Base Item | 500+ | 500+ | ✓ OK |
| Base Item-NUT | 500+ | 0 | ✗ MISSING |

Every single order has the base item invoiced but the "-NUT" accessory missing from the invoice.

---

## Understanding "Shipped But Not Invoiced"

In MRPeasy terminology:

- **Shipped**: Physically sent to customer (qty > 0)
- **Not Invoiced**: No invoice line created for it

Examples from C89084:
- **Items from Past Shipment** (March 20):
  - 32169: Shipped 250 → Invoiced 250 ✓
  - 76003: Shipped 500 → Invoiced 500 ✓
  - 76003-NUT: Shipped 500 → Invoiced 0 ✗
  
- **Items from Future Shipment** (April 14):
  - 76003: Shipped 0 → Invoiced 0 ✓ (OK, not shipped yet)
  - 76003-NUT: Shipped 0 → Invoiced 0 ✓ (OK, not shipped yet)

---

## Why "-NUT" Items Are Separate Line Items

In bulk/manufacturing orders, items often come in bundled "kits":
- The base item is the main product (76003)
- The "-NUT" items are accessories (like fasteners, connectors)
- They're separate SKUs for tracking/costing purposes
- But they're always shipped/invoiced together

MRPeasy likely keeps them as separate line items because:
1. Different suppliers or manufacturing processes
2. Different internal costs
3. Inventory tracking purposes
4. Customer wants to see item detail

But when creating invoices, both should be included if both were shipped.

---

## Key Takeaway

**The issue is NOT with how the order is structured.**

The order structure makes perfect sense:
- Multiple delivery dates → separate lines ✓
- Bundled items as separate SKUs → separate lines ✓  
- Clear tracking of shipments → good ✓

**The issue IS that invoices don't include all line items that were shipped.**

When shipment 2 (March 20) shipped, 4 line items shipped:
- 32169 ✓ in invoice
- 79299-HPC ✓ in invoice  
- 76003 ✓ in invoice
- 76003-NUT ✗ NOT in invoice (BUG)


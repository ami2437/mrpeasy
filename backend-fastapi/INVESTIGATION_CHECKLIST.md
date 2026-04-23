# INVESTIGATION CHECKLIST: Invoice Inv-9601564 "-NUT" Item Missing

## Phase 1: Verify the Issue (5 min)

- [ ] Log into MRPeasy
- [ ] Find invoice Inv-9601564
- [ ] Click to view all products in the invoice
- [ ] Check if products include item with code containing "-NUT" (e.g., "76003-NUT")
- [ ] Compare with order C89084 to verify

**Result Note**: _______________

---

## Phase 2: Check Item Configuration in MRPeasy (10 min)

### Check Item Settings
- [ ] Go to Products/Items section in MRPeasy
- [ ] Find item 76003-NUT
- [ ] Review item details:
  - [ ] Item Type / Category field
  - [ ] Is it marked as "Accessory", "Component", "Part", "Non-billable", etc.?
  - [ ] Any "Invoice" or "Exclude from invoice" checkbox?
  - [ ] Any supplier/classification that might exclude it?
- [ ] Take note of any special settings

**Items with Special Settings**: _______________

---

## Phase 3: Check Invoice Generation Rules (10 min)

### MRPeasy Settings
- [ ] Look for Settings > Invoicing or Settings > Invoice Rules
- [ ] Check if there's a rule about item types to include/exclude
- [ ] Look for any rules about:
  - [ ] Supplementary items
  - [ ] Bundled items
  - [ ] Items with specific codes or suffixes
  - [ ] Non-billable items
- [ ] Note any filters or exclusion rules

**Rules Found**: _______________

### Check Invoice Templates
- [ ] If there are invoice templates, check if they have item filters
- [ ] Check if template is excluding certain product types

---

## Phase 4: Review API Documentation (10 min)

### MRPeasy API Docs
- [ ] Check MRPeasy API documentation for /invoices endpoint
- [ ] Look for parameters like:
  - [ ] filter by item type
  - [ ] include_products vs exclude_products
  - [ ] item_filters or item_types
- [ ] Check if there's a known limitation or behavior

**API Behavior**: _______________

---

## Phase 5: Check All 36 Affected Orders (Optional but Thorough)

### Pattern Verification
- [ ] Open 3 more invoices from the list below
- [ ] For each, check if "-NUT" items are missing:

Orders to check:
- [ ] C89091 / Inv-9601561 (should be missing 69474-NUT, 69473-NUT)
- [ ] C89086 / Inv-9601532 (should be missing 79647-NUT, 30745-NUT)  
- [ ] C89085 / Inv-9601533 (should be missing 39222-NUT, 79647-NUT, etc.)

**Pattern confirmed**: YES / NO

---

## Phase 6: Determine The Cause

Based on your findings above, is the issue:

**A) Item Configuration** (items marked as non-billable/accessory)
- [ ] "-NUT" items have special type/flag
- [ ] MRPeasy is correctly excluding them per configuration
- **Decision**: Change item type or update invoice rules

**B) Intentional Invoice Rule** (MRPeasy configured to exclude these)
- [ ] Settings explicitly exclude "-NUT" items or item type
- [ ] This is by design in MRPeasy
- **Decision**: Either change rules or accept as design

**C) API Bug** (MRPeasy API not returning these items)
- [ ] Items exist in MRPeasy invoice but API doesn't return them
- [ ] Filtering happening on API side
- **Decision**: Contact MRPeasy support

**D) Unknown** (something else)
- [ ] Can't determine from settings/documentation
- **Decision**: Contact MRPeasy support

**Most Likely Cause**: A / B / C / D

---

## Phase 7: Plan Next Steps

### If Cause is A or B (Configuration)
- [ ] Decide: Should "-NUT" items be invoiced? (YES/NO)
- [ ] If YES:
  - [ ] Change item type/flag in MRPeasy
  - [ ] Change invoice rules
  - [ ] Re-generate invoices for 36 affected orders
- [ ] If NO:
  - [ ] Document that "-NUT" items are intentionally not invoiced
  - [ ] Update our system to not flag them as under-invoiced

### If Cause is C (API Bug)
- [ ] Contact MRPeasy Support with:
  - [ ] Order numbers: C89084, C89091, C89086, C89085, C89081
  - [ ] Invoice numbers: Inv-9601564, Inv-9601561, Inv-9601532, Inv-9601533, Inv-9601562
  - [ ] Description: "-NUT" items shipped but not in API invoice data
  - [ ] Question: Are "-NUT" items supposed to be in invoice products?

### If Cause is D (Unknown)
- [ ] Document findings
- [ ] Contact MRPeasy Support
- [ ] Escalate if needed

---

## Phase 8: Implement Fix

### If Decision is to Include "-NUT" Items
```python
# File: backend-fastapi/app/routes/invoicing.py
# No changes needed - backend already handles them correctly
# Fix happens in MRPeasy configuration
```

### If Decision is to Exclude "-NUT" Items
```python
# File: backend-fastapi/app/routes/invoicing.py
# Around line 676, add to product filtering:

if item_code and str(item_code).lower().endswith('-nut'):
    continue  # Exclude -NUT items from invoicing checks
```

### If Decision is to Document Discrepancy
Update discrepancy alerts to note:
```
"Note: Items ending in '-NUT' are not included in invoices by design"
```

---

## Phase 9: Validation

- [ ] Re-run diagnostic script: `python diagnose_mismatches.py`
- [ ] Verify result shows reduced or zero "-NUT" discrepancies
- [ ] Spot-check 5 orders from the 36 affected
- [ ] Verify invoices now include (or correctly exclude) "-NUT" items

---

## Phase 10: Documentation & Communication

- [ ] Update internal documentation with findings
- [ ] Brief team on the issue and resolution
- [ ] Update invoice processing documentation
- [ ] Test with next month's orders to ensure fix works going forward

---

## Decision Tree

```
Is 76003-NUT in MRPeasy invoice Inv-9601564?

├─ YES (API is filtering it)
│  ├─ Check if -NUT items have special type/flag
│  │  ├─ YES → Change item config (Phase 7A)
│  │  └─ NO → Check invoice rules (Phase 7B)
│  └─ If still unknown → Contact MRPeasy (Phase 7C)
│
└─ NO (MRPeasy created invoice without it)
   ├─ Check if -NUT items configured as non-billable
   │  ├─ YES → Decide policy (Phase 7A)
   │  └─ NO → Check invoice rules (Phase 7B)
   └─ If still unknown → Contact MRPeasy (Phase 7C)
```

---

## Questions to Ask MRPeasy Support

If you need to contact them:

1. "Why aren't items ending in '-NUT' appearing in invoice Inv-9601564 when they're in order C89084 and were shipped?"

2. "Are there item types, flags, or invoice rules that exclude items from invoice line items?"

3. "Is there a filter in the /invoices API endpoint that excludes certain products?"

4. "How should bundled items or accessories be handled in invoicing?"

5. "Is this behavior intentional or a known issue?"

---

## Expected Outcomes

| Scenario | Resolution | Effort |
|----------|-----------|--------|
| Items should be invoiced | Fix MRPeasy config + recreate invoices | 2-4 hours |
| Items should NOT be invoiced | Update our discrepancy detection | 30 min |
| API bug | Wait for MRPeasy fix | TBD |
| Configuration change needed | Adjust settings + re-test | 1-2 hours |

---

## Timeline

- **Today**: Complete Phases 1-4 (determine cause)
- **This week**: Implement fix (Phase 8)
- **Next week**: Validate (Phase 9) and communicate (Phase 10)

---

**Contact Info if Stuck**:
- MRPeasy Support: [support contact]
- Diagnostic script: `backend-fastapi/diagnose_mismatches.py`
- Detailed analysis: `backend-fastapi/FINAL_BUG_ANALYSIS.md`

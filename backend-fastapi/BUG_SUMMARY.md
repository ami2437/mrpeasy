# SUMMARY: What You Need to Know About Invoice Inv-9601564 Bug

## The Issue (Simplest Version)

Order C89084 shows item 76003-NUT was shipped 500 units, but it's not in invoice Inv-9601564. 

This happens to 36 out of 100 orders - ALL items ending in "-NUT" are affected.

---

## Is It Our Fault?

**NO.** Our code is working correctly. The invoices returned by MRPeasy are missing the "-NUT" items.

### Evidence:
- Backend code just passes through what MRPeasy's API returns
- No filters or exclusions in our code for "-NUT" items
- Our discrepancy detection correctly flags 76003-NUT as missing

---

## What's Missing?

That's what you need to find out by checking MRPeasy:
- Are "-NUT" items configured as "non-billable"?
- Is there a rule excluding them from invoices?
- Is it a bug in MRPeasy's invoice generation?

---

## What You Should Do

1. **Check MRPeasy invoice Inv-9601564 directly** - does it have 76003-NUT?
2. **If NO** - contact MRPeasy about why "-NUT" items aren't in invoices
3. **If YES** - let me know and we'll dig into our code (unlikely)

---

## Impact

- 36 orders with missing invoiced items
- Hundreds of units shipped but not billed
- Reports correctly show under-invoiced status

---

## Next File to Review

See **FINAL_BUG_ANALYSIS.md** for complete technical details and step-by-step investigation guide.

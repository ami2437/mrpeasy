# Packing Slip Format - Matching Your Example

Based on the PDF example you provided (SH215598-P.pdf), here's what the system will generate:

## Layout Comparison

### Your Current Format:
```
┌─────────────────────────────────────────────────────┐
│              PACKING SLIP / LIST                     │
├─────────────────────────────────────────────────────┤
│                                                     │
│  Shipment #: SH215598                               │
│  Date: [date]                                       │
│  Customer: American Traders LLC                     │
│  PO #: [order reference]                            │
│                                                     │
├─────────────────────────────────────────────────────┤
│  Item #  │ Description    │ Qty  │ Packing Details │
├──────────┼────────────────┼──────┼─────────────────┤
│ [item]   │ [description]  │ [qty]│ [box info]      │
│          │                │      │                 │
│ [item]   │ [description]  │ [qty]│ [box info]      │
│          │                │      │                 │
└─────────────────────────────────────────────────────┘
```

### Our Implementation:
```
┌─────────────────────────────────────────────────────────────┐
│                    PACKING SLIP                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────┬──────────┬──────────┬──────────────────────┐  │
│  │Shipment #│ PO #     │ Date     │ Customer             │  │
│  │ SH215601 │PO#123456 │02/02/2026│ American Traders LLC │  │
│  └──────────┴──────────┴──────────┴──────────────────────┘  │
│                                                             │
│  SUMMARY:                                                   │
│    • Total Items: 2                                         │
│    • Total Qty Shipped: 150                                 │
│    • Items with Multiple Order Lines: test_1_bolt (2 lines) │
│                                                             │
├─────────────────┬──────────┬──────┬──────────┬──────────────┤
│ Item Code /     │ PO #     │ Line │ Qty Shp  │ Box Details  │
│ Description     │          │      │          │              │
├─────────────────┼──────────┼──────┼──────────┼──────────────┤
│ test_1_bolt     │ PO#      │  1   │  100     │ 3 box 30     │
│ test-bolt       │ 123456   │      │          │ 2 box 5      │
├─────────────────┼──────────┼──────┼──────────┼──────────────┤
│ test_1_bolt     │ PO#      │  2   │  50      │ 1 box 50     │
│ test-bolt       │ 123456   │      │          │              │
├─────────────────┴──────────┴──────┴──────────┴──────────────┤
│                                                             │
│  Packed By: _______________  Checked By: ____________      │
│                                                             │
│  Shipped By: _______________                                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Packing Slip Display Features

### Header Section ✅
- [x] Shipment Code (SH215601)
- [x] PO Number (PO # 123456)
- [x] Date Finalized (02/02/2026)
- [x] Customer Name (American Traders LLC)

### Summary Section ✅
- [x] Total number of line items
- [x] Total quantity shipped (sum of all items)
- [x] List of items with multiple order lines

### Items Table ✅
| Column | Content | Example |
|--------|---------|---------|
| Item Code | Product code | test_1_bolt |
| Description | Product name | test-bolt |
| PO # | Purchase order number | PO # 123456 |
| Order Line | Order line number | 1, 2 |
| Qty Shipped | Total units in shipment | 100, 50 |
| Box Breakdown | How items are boxed | 3 box of 30, 2 box of 5 |

### Footer Section ✅
- [x] Packed By (signature line)
- [x] Checked By (signature line)
- [x] Shipped By (signature line)

## Key Difference from Traditional Formats

Your request wanted automatic grouping and combining of duplicate items by order line.

**Before our system:**
```
Individual box records:
  Box 1: 30 units
  Box 2: 30 units
  Box 3: 30 units
  Box 4: 5 units
  Box 5: 5 units
```

**After our system:**
```
Grouped display:
  Qty Shipped: 100
  Box Breakdown: 3 box of 30, 2 box of 5
```

This makes the packing slip more readable and concise for warehouse staff.

## Printing

When printed or saved as PDF:

```
┌────────────────────────────────────────────┐
│          PACKING SLIP                      │
│     American Traders LLC                   │
│     ATind Supplies                         │
│                                            │
│  Shipment: SH215601                        │
│  Date: February 2, 2026                    │
│  PO #: PO # 123456                         │
│                                            │
│  Items:                                    │
│  ┌──────────────┬────┬──────────────────┐  │
│  │ test_1_bolt  │ 100│ 3 box 30         │  │
│  │ test-bolt    │    │ 2 box 5          │  │
│  ├──────────────┼────┼──────────────────┤  │
│  │ test_1_bolt  │ 50 │ 1 box 50         │  │
│  │ test-bolt    │    │                  │  │
│  └──────────────┴────┴──────────────────┘  │
│                                            │
│  Packed By: _____________                  │
│  Checked By: _____________                 │
│  Shipped By: _____________                 │
│                                            │
└────────────────────────────────────────────┘
```

## Data Coming From

| Field | Source |
|-------|--------|
| Shipment Code | From shipment in MRPeasy |
| PO Number | From customer order reference in MRPeasy |
| Item Code | From shipment product |
| Description | From shipment product title |
| Qty Shipped | Sum of all box quantities in database |
| Box Breakdown | Calculated from individual boxes in database |
| Order Line | From order line data in database |
| Date | When shipment was finalized |

## Workflow

1. **User finalizes shipment** → Data saved to database
2. **System generates packing slip** → Groups by order line
3. **Warehouse views packing slip** → Professional, readable format
4. **Print or save as PDF** → Attach to shipment
5. **Staff signs** → Workflow tracking

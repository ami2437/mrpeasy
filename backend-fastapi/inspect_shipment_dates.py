"""
Diagnostic: inspect what date fields MRPeasy returns on shipment records.
Run from backend-fastapi with the venv active:
    python inspect_shipment_dates.py
"""
import sys, json
from datetime import datetime

sys.path.insert(0, 'c:/mrpeasy/backend-fastapi')
from app.services.mrpeasy_client import mrpeasy_client

print("Fetching shipments from MRPeasy API …")
shipments = mrpeasy_client.get_shipments()
print(f"Total shipments returned: {len(shipments)}\n")

if not shipments:
    print("No shipments found.")
    sys.exit(0)

# ── Show all keys from the first shipment ─────────────────────────────────────
first = shipments[0]
print("=" * 70)
print("ALL KEYS IN FIRST SHIPMENT RECORD")
print("=" * 70)
for k, v in first.items():
    print(f"  {k!r:40s}: {v!r}")

# ── Focus on every key that looks date-related across first 10 shipments ───────
date_keywords = ('date', 'time', 'created', 'modified', 'shipped', 'delivery',
                 'dispatch', 'sent', 'closed', 'completed')

print("\n" + "=" * 70)
print("DATE-LIKE FIELDS (first 10 shipments)")
print("=" * 70)

sample = shipments[:10]
date_keys_seen = set()
for s in sample:
    for k in s:
        if any(kw in k.lower() for kw in date_keywords):
            date_keys_seen.add(k)

for s in sample:
    code = s.get('code') or s.get('shipment_id') or '?'
    print(f"\n  Shipment: {code}")
    for k in sorted(date_keys_seen):
        raw = s.get(k)
        # Try to decode unix timestamps
        display = raw
        if isinstance(raw, (int, float)) and 1_000_000_000 < raw < 9_999_999_999:
            try:
                display = f"{raw}  →  {datetime.utcfromtimestamp(raw).strftime('%Y-%m-%d %H:%M UTC')}"
            except Exception:
                pass
        print(f"    {k!r:35s}: {display!r}")

# ── Show the linked order info for first shipment ─────────────────────────────
print("\n" + "=" * 70)
print("ORDER LINKAGE FIELDS (first shipment)")
print("=" * 70)
for k in ('customer_order_id', 'cust_ord_id', 'customer_order_code',
          'orders', 'code', 'status', 'status_txt'):
    print(f"  {k!r:35s}: {first.get(k)!r}")

"""Download everything from MRPeasy into a frozen snapshot folder.

READ-ONLY: this module only ever issues GET requests. Every dry-run load reads the
snapshot, never the live API, so repeated test imports see exactly the same data.

    python -m app.importers.mrpeasy extract
"""
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

import httpx

SNAPSHOT_ROOT = Path(__file__).resolve().parents[3] / "import-data" / "mrpeasy"
PAGE = 100

# name -> endpoint. Every list endpoint already includes its lines / addresses / contacts.
ENDPOINTS = {
    "product_groups": "/product-groups",
    "units": "/units",
    "items": "/items",
    "customers": "/customers",
    "vendors": "/vendors",
    "purchase_orders": "/purchase-orders",
    "lots": "/lots",
    "customer_orders": "/customer-orders",
    "shipments": "/shipments",
    "invoices": "/invoices",
    "inventory": "/stock/inventory",
}


def _credentials():
    """The MRPeasy key lives in the old backend's .env (or AT-HUB's own env vars)."""
    key, secret = os.getenv("MRPEASY_API_KEY", ""), os.getenv("MRPEASY_API_SECRET", "")
    base = os.getenv("MRPEASY_API_BASE_URL", "https://api.mrpeasy.com/rest/v1")
    env_file = Path(__file__).resolve().parents[4] / "backend-fastapi" / ".env"
    if not key and env_file.exists():
        env = dict(l.split("=", 1) for l in env_file.read_text().splitlines() if "=" in l and not l.lstrip().startswith("#"))
        get = lambda k: env.get(k, "").strip().strip('"').strip("'")
        key, secret, base = get("MRPEASY_API_KEY"), get("MRPEASY_API_SECRET"), get("MRPEASY_API_BASE_URL") or base
    if not key:
        raise SystemExit("No MRPeasy API key: set MRPEASY_API_KEY / MRPEASY_API_SECRET")
    return base.rstrip("/"), key, secret


class ReadOnlyClient:
    def __init__(self):
        self.base, key, secret = _credentials()
        self.http = httpx.Client(auth=(key, secret), timeout=60)
        self.requests = 0

    def get(self, endpoint: str, headers=None):
        for attempt in range(6):
            self.requests += 1
            r = self.http.get(self.base + endpoint, headers=headers or {})
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(float(r.headers.get("Retry-After", 2 ** attempt)))
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()

    def get_all(self, endpoint: str) -> list:
        """Page through a list with Range headers until the reported total is reached."""
        rows, start = [], 0
        while True:
            r = self.get(endpoint, {"Range": f"items={start}-{start + PAGE - 1}"})
            batch = r.json() if r.text else []
            if isinstance(batch, dict):
                batch = [batch]
            rows.extend(batch)
            total = (r.headers.get("Content-Range") or "").split("/")[-1]
            total = int(total) if total.isdigit() else None
            if not batch or len(batch) < PAGE or (total is not None and len(rows) >= total):
                break
            start += PAGE
            time.sleep(0.3)  # be gentle with their API
        if total is not None and len(rows) != total:
            raise SystemExit(f"{endpoint}: got {len(rows)} rows but MRPeasy reports {total}")
        return rows


def extract() -> Path:
    client = ReadOnlyClient()
    folder = SNAPSHOT_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True)
    manifest = {"taken_at": datetime.now().isoformat(timespec="seconds"), "source": client.base, "files": {}}
    for name, endpoint in ENDPOINTS.items():
        rows = client.get_all(endpoint)
        data = json.dumps(rows, indent=1, ensure_ascii=False)
        (folder / f"{name}.json").write_text(data, encoding="utf-8")
        manifest["files"][name] = {"rows": len(rows), "sha256": hashlib.sha256(data.encode()).hexdigest()}
        print(f"  {name:16} {len(rows):5} rows")
    manifest["api_requests"] = client.requests
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"Snapshot saved: {folder}  ({client.requests} read-only requests)")
    return folder


def latest_snapshot() -> Path:
    snaps = sorted(p for p in SNAPSHOT_ROOT.glob("*") if (p / "manifest.json").exists())
    if not snaps:
        raise SystemExit("No snapshot yet: run  python -m app.importers.mrpeasy extract")
    return snaps[-1]

"""Download a backup to your own computer: Super admin only by default, a real working copy, due every 3 days."""
import io
import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.test_roles import _user


def _zip(client, headers, files=True, saved=True):
    r = client.get(f"/api/local-backup/download?files={'true' if files else 'false'}", headers=headers)
    assert r.status_code == 200, r.text[:300]
    if saved:  # what the screen does once the browser has written the file
        assert client.post("/api/local-backup/saved", json={"files": files, "size": len(r.content)}, headers=headers).status_code == 200
    return zipfile.ZipFile(io.BytesIO(r.content)), r


def test_everything_zip_holds_a_working_database_and_the_files(client, admin_headers, make, api):
    a = make.item()
    make.stock(a, 1)
    o = make.order(lines=[(a, 1, 1)])
    sh = make.ship(o)
    client.post("/api/attachments/", headers=admin_headers, data={"entity_type": "shipment", "entity_id": str(sh["id"]), "category": "pod"},
                files={"files": ("pod.pdf", b"%PDF-1.4 test", "application/pdf")})
    z, r = _zip(client, admin_headers)
    names = z.namelist()
    assert "at_hub.db" in names and "README.txt" in names and any(n.startswith("uploads/") and n.endswith("pod.pdf") for n in names)
    assert r.headers["content-disposition"].endswith('everything.zip"') or "everything.zip" in r.headers["content-disposition"]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "at_hub.db"
        p.write_bytes(z.read("at_hub.db"))
        c = sqlite3.connect(p)
        assert c.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert c.execute("select count(*) from customer_orders where id = ?", (o["id"],)).fetchone()[0] == 1
        c.close()


def test_database_only_leaves_the_files_out(client, admin_headers):
    z, _ = _zip(client, admin_headers, files=False)
    assert "at_hub.db" in z.namelist() and not any(n.startswith("uploads/") for n in z.namelist())


def test_due_every_three_days_and_a_download_resets_it(client, admin_headers):
    from app.config.database import SessionLocal
    from app.models import AppSetting
    before = client.get("/api/local-backup/status", headers=admin_headers).json()["last"]
    _zip(client, admin_headers, files=False, saved=False)                  # downloaded but never saved: doesn't count
    assert client.get("/api/local-backup/status", headers=admin_headers).json()["last"] == before
    _zip(client, admin_headers, files=False)
    s = client.get("/api/local-backup/status", headers=admin_headers).json()
    assert s["due"] is False and s["last"]["by"] == "admin" and s["due_days"] == 3
    db = SessionLocal()
    try:  # pretend the last one was 4 days ago
        row = db.get(AppSetting, "last_local_backup")
        v = json.loads(row.value)
        v["at"] = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(timespec="seconds")
        row.value = json.dumps(v)
        db.commit()
    finally:
        db.close()
    assert client.get("/api/local-backup/status", headers=admin_headers).json()["due"] is True
    _zip(client, admin_headers, files=False)
    assert client.get("/api/local-backup/status", headers=admin_headers).json()["due"] is False


def test_super_admin_only_until_ticked_for_a_role(client, admin_headers):
    from app.services import permissions as P
    assert "backups.download" in P.KEYS and "backups.download" not in P.defaults_for("admin")
    h = _user(client, admin_headers, "admin")
    assert client.get("/api/local-backup/status", headers=h).status_code == 403
    assert client.get("/api/local-backup/download", headers=h).status_code == 403
    assert client.post("/api/local-backup/saved", json={"files": True, "size": 5}, headers=h).status_code == 403

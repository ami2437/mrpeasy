"""Database backups: make, list, restore (with a safety copy first)."""


def test_backup_and_restore(make, api):
    name = api.post("/api/backups/")["name"]
    assert name.endswith("-manual.db")
    c = make.customer(name="Before-Restore Customer")
    lst = api.get("/api/backups/")["backups"]
    assert any(b["name"] == name for b in lst)
    r = api.post(f"/api/backups/{name}/restore")
    assert r["safety_copy"].endswith("-pre-restore.db")
    names = [x["name"] for x in api.get("/api/customers/")]
    assert "Before-Restore Customer" not in names        # back to the moment of the backup
    api.post(f"/api/backups/{r['safety_copy']}/restore")  # and the safety copy undoes it
    assert "Before-Restore Customer" in [x["name"] for x in api.get("/api/customers/")]
    api.post("/api/backups/../at_hub.db/restore", expect=(404, 405))

"""File Matcher: attach a folder's PDFs only where the number in the file name is certain."""
import random

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def test_scan_and_attach_only_certain(make, api, tmp_path):
    a = make.item()
    n1, n2, n3 = (str(random.randint(5_000_000, 9_999_999)) for _ in range(3))
    o1 = make.order(lines=[(a, 1, 1)], po_number=n1)
    o2 = make.order(lines=[(a, 1, 1)], po_number=n2)
    make.order(lines=[(a, 1, 1)], po_number=n3); make.order(lines=[(a, 1, 1)], po_number=n3)  # same PO # twice
    (tmp_path / "2025").mkdir()
    for name in (f"2025/CHART_PO_{n1}.PDF", f"CHART_PO_{n2}.pdf", f"CHART_PO_{n2} rev1.pdf", f"CHART_PO_{n3}.pdf",
                 f"CHART_PO_{n1}9.pdf", "random scan.pdf"):
        (tmp_path / name).write_bytes(PDF)
    rows = {r["file"].replace("\\", "/"): r for r in api.post("/api/file-matcher/scan", json={"path": str(tmp_path), "kind": "customer"})["files"]}
    assert rows[f"2025/CHART_PO_{n1}.PDF"]["status"] == "match" and rows[f"2025/CHART_PO_{n1}.PDF"]["record_id"] == o1["id"]
    assert rows[f"CHART_PO_{n2}.pdf"]["status"] == "ambiguous"          # two files for one order
    assert rows[f"CHART_PO_{n3}.pdf"]["status"] == "ambiguous"          # PO # on two orders
    assert rows[f"CHART_PO_{n1}9.pdf"]["status"] == "no_match"          # a longer number isn't the PO #
    assert rows["random scan.pdf"]["status"] == "no_match"
    files = [r["file"] for r in rows.values()]
    res = api.post("/api/file-matcher/attach", json={"path": str(tmp_path), "kind": "customer", "files": files})
    assert [x["record_id"] for x in res["attached"]] == [o1["id"]] and len(res["skipped"]) == 5
    att = api.get(f"/api/attachments/?entity_type=customer_order&entity_id={o1['id']}")
    assert [x["category"] for x in att] == ["customer_po"]
    assert api.get("/api/attachments/counts?entity_type=customer_order")[str(o1["id"])] == 1
    assert str(o2["id"]) not in api.get("/api/attachments/counts?entity_type=customer_order")
    # run again: nothing new
    again = {r["file"].replace("\\", "/"): r for r in api.post("/api/file-matcher/scan", json={"path": str(tmp_path), "kind": "customer"})["files"]}
    assert again[f"2025/CHART_PO_{n1}.PDF"]["status"] == "attached"


def test_vendor_files_by_so_number(make, api, tmp_path):
    a = make.item()
    so = f"SO{random.randint(100000, 999999)}"
    po = make.po(lines=[(a, 2, 1)], vendor_so_number=so)
    (tmp_path / f"Invoice {so}.pdf").write_bytes(PDF)
    (tmp_path / f"{po['code']} confirmation.pdf").write_bytes(PDF)
    rows = api.post("/api/file-matcher/scan", json={"path": str(tmp_path), "kind": "vendor"})["files"]
    assert {r["status"] for r in rows} == {"match"} and {r["record_id"] for r in rows} == {po["id"]}
    res = api.post("/api/file-matcher/attach", json={"path": str(tmp_path), "kind": "vendor", "files": [r["file"] for r in rows], "category": "auto"})
    assert sorted(x["category"] for x in res["attached"]) == ["vendor_invoice", "vendor_quote"]


def test_bad_folder(api):
    api.post("/api/file-matcher/scan", json={"path": "C:/no/such/folder", "kind": "customer"}, expect=400)

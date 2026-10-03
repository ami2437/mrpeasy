"""CSV import: preview never saves; apply creates / updates; bad rows are skipped with a reason."""


def upload(client, headers, kind, step, text):
    return client.post(f"/api/import/{kind}/{step}", headers=headers, files={"file": ("in.csv", text.encode("utf-8-sig"), "text/csv")})


def test_items_preview_then_apply(client, admin_headers, api):
    csv = ("Item code,Description,Product group,Price,Cost,Reorder point,Pack size\n"
           "CSV-1,Hex bolt 1/2,bolt,$1,234.50,0.80,10,50\n"   # quoted money below
           )
    csv = ('Item code,Description,Product group,Price,Cost,Reorder point,Pack size\n'
           'CSV-1,Hex bolt 1/2,bolt,"$1,234.50",0.80,10,50\n'
           'CSV-2,Washer,Washer,0.19,,,\n'
           'CSV-3,Mystery,Fastener,1,1,,\n'          # unknown group
           ',No code,Nut,1,1,,\n')                    # no code
    p = upload(client, admin_headers, "items", "preview", csv).json()
    assert p["counts"] == {"create": 2, "update": 0, "same": 0, "error": 2}
    assert api.get("/api/stock-items/?q=CSV-1") == []                       # preview saves nothing
    r = upload(client, admin_headers, "items", "apply", csv).json()
    assert r == {"created": 2, "updated": 0, "skipped": 2}
    it = api.get("/api/stock-items/?q=CSV-1")[0]
    assert (it["category"], it["selling_price"], it["default_pack_size"]) == ("Bolt", 1234.50, 50)
    # second run: one change -> update; the rest unchanged
    r = upload(client, admin_headers, "items", "apply", csv.replace('"$1,234.50"', "2.00")).json()
    assert r["updated"] == 1 and r["created"] == 0
    assert api.get("/api/stock-items/?q=CSV-1")[0]["selling_price"] == 2.0


def test_customers_and_vendors_upsert_by_name(client, admin_headers, api):
    csv = "Name,Contact,Email,Phone\nCSV Customer A,Pat,pat@example.com,555-0100\n"
    assert upload(client, admin_headers, "customers", "apply", csv).json()["created"] == 1
    assert upload(client, admin_headers, "customers", "apply", csv.replace("Pat,", "Sam,")).json()["updated"] == 1
    c = [x for x in api.get("/api/customers/") if x["name"] == "CSV Customer A"][0]
    assert c["contact_name"] == "Sam"
    v = upload(client, admin_headers, "vendors", "apply", "Vendor,Phone\nCSV Vendor Z,555-0199\n").json()
    assert v["created"] == 1
    assert [x for x in api.get("/api/vendors/") if x["name"] == "CSV Vendor Z"][0]["code"].startswith("V")


def test_file_without_a_key_column_is_refused(client, admin_headers):
    r = upload(client, admin_headers, "items", "preview", "Foo,Bar\n1,2\n")
    assert r.status_code == 400 and "code" in r.json()["detail"]

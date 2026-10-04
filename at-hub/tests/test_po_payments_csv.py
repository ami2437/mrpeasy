"""PO payments from MRPeasy's PO export: record the difference, never double-count."""


def csv_for(rows):
    head = "Number,Total,Tax,Total including tax,Paid,Unpaid,Status,Invoice status,Payment status,Invoice ID,Invoice date,Due date,Order date,Status\n"
    return (head + "".join(f'{c},{t},0,{t},{p},{t - p},Received,Invoiced,{st},"{inv}","{d}","{d}",1/1/2026,Closed\n'
                           for c, t, p, st, inv, d in rows)).encode()


def test_preview_apply_reapply(make, api, monkeypatch, tmp_path):
    from app.services import po_payments_csv
    monkeypatch.setattr(po_payments_csv, "SAVED", tmp_path / "po_payments.csv")
    a = make.item()
    po1 = make.po(lines=[(a, 10, 10)])  # $100
    api.post(f"/api/purchase-orders/{po1['id']}/bills", json={"bill_number": "B-1", "amount": 100})
    po2 = make.po(lines=[(a, 10, 20)])  # $200, half paid
    data = csv_for([(po1["code"], 100, 100, "Paid", "B-1", "2/1/2026"), (po2["code"], 200, 100, "Paid partially 50%", "X-1, \nX-2", "2/1/2026, \n3/1/2026"),
                    ("PO-NOPE", 5, 5, "Paid", "", "")])
    prev = {r["code"]: r for r in api.post("/api/purchase-orders/payments-import/preview", files={"file": ("po.csv", data, "text/csv")})}
    assert prev[po1["code"]]["action"] == "add" and prev[po1["code"]]["bill"] == "B-1"
    assert prev[po2["code"]]["add"] == 100 and prev[po2["code"]]["date"] == "2026-03-01"  # latest due date
    assert prev["PO-NOPE"]["action"] == "skip"
    r = api.post("/api/purchase-orders/payments-import/apply", files={"file": ("po.csv", data, "text/csv")})
    assert r == {"payments": 2, "amount": 200.0}
    p1 = api.get(f"/api/purchase-orders/{po1['id']}")
    assert sum(p["amount"] for p in p1["payments"]) == 100 and p1["bills"][0]["balance"] == 0
    again = api.post("/api/purchase-orders/payments-import/apply", files={"file": ("po.csv", data, "text/csv")})
    assert again["payments"] == 0  # second upload: nothing new
    assert po_payments_csv.SAVED.exists()


def test_not_the_export(api):
    api.post("/api/purchase-orders/payments-import/preview", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}, expect=400)

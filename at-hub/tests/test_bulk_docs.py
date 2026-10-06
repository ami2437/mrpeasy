"""Bulk Operations -> Send / Print Documents: packing lists, labels and invoices for many shipments, one email per
order (draft invoices become sent, every email is logged), or one merged PDF to print."""
import io

from pypdf import PdfReader


class FakeSMTP:
    sent = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, **k):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg, to_addrs=None):
        FakeSMTP.sent.append(msg)


def test_send_and_print_documents(api, client, admin_headers, make, monkeypatch):
    from app.config.settings import settings
    from app.services import email as email_service
    a = make.item(price=4)
    make.stock(a, 40)
    cust = make.customer(email="dock@example.com", details={"emails": [{"label": "Accounts payable", "value": "ap@example.com"}]})
    o = make.order(customer=cust, lines=[(a, 40, 4)])
    sh1 = make.ship(o, {o["lines"][0]["id"]: 25})
    sh2 = make.ship(api.get(f"/api/customer-orders/{o['id']}"), {o["lines"][0]["id"]: 15})
    inv = make.invoice([sh1, sh2])
    ids = [sh1["id"], sh2["id"]]
    kinds = ["packing_list", "labels", "invoice"]

    p = api.post("/api/bulk/documents/plan", json={"shipment_ids": ids, "kinds": kinds})
    assert len(p["groups"]) == 1                                   # one order -> one email
    g = p["groups"][0]
    assert g["to"] == "ap@example.com"                             # an invoice goes to the billing address
    assert sorted(x["kind"] for x in g["attachments"]) == ["invoice", "labels", "labels", "packing_list", "packing_list"]

    r = client.get(f"/api/bulk/documents.pdf?shipment_ids={ids[0]},{ids[1]}&kinds=packing_list,labels,invoice", headers=admin_headers)
    assert r.status_code == 200 and len(PdfReader(io.BytesIO(r.content)).pages) >= 4

    monkeypatch.setattr(settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(settings, "smtp_security", "none")
    monkeypatch.setattr(settings, "smtp_from", "sales@test.example")
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent = []
    res = api.post("/api/bulk/documents/send", json={"shipment_ids": ids, "kinds": kinds, "edits": {g["key"]: {"cc": "boss@example.com"}}})["results"]
    assert res[0]["ok"], res
    msg = FakeSMTP.sent[0]
    names = sorted(part.get_filename() for part in msg.iter_attachments())
    po = o["po_number"]                                                           # <record #>-<customer PO #>-<what>.pdf
    assert names == sorted([f"{sh1['code']}-{po}-Packing List.pdf", f"{sh2['code']}-{po}-Packing List.pdf", f"{sh1['code']}-{po}-Labels.pdf",
                            f"{sh2['code']}-{po}-Labels.pdf", f"{inv['code']}-{po}-Invoice.pdf"])
    assert msg["To"] == "ap@example.com" and msg["Cc"] == "boss@example.com"
    assert api.get(f"/api/invoices/{inv['id']}")["status"] == "sent"            # sending an invoice makes it sent
    assert len(api.get(f"/api/shipments/{sh1['id']}/pod-emails")) == 1             # logged on the shipment


def test_documents_need_the_right_permission(client, admin_headers, make):
    from app.services.auth import AuthService
    client.post("/api/users/", json={"username": "drv-bulk", "password": "Str0ng!Passw0rd#", "role": "driver"}, headers=admin_headers)
    h = {"Authorization": f"Bearer {AuthService.create_access_token({'sub': 'drv-bulk'})}"}
    assert client.post("/api/bulk/documents/plan", json={"shipment_ids": [1], "kinds": ["packing_list"]}, headers=h).status_code == 403


def test_shipment_numbers_job_suffix_rename_and_file_names(api, client, admin_headers, make):
    """New shipments: <next #>-<job #>; the series still counts them; shipment / invoice # can be changed (unique);
    PDFs are named <#>-<PO #>-<what>.pdf and ?as_link=1 hands out a link under that name."""
    a = make.item()
    make.stock(a, 50)
    o = make.order(lines=[(a, 10, 2)])
    api.put(f"/api/customer-orders/{o['id']}", json={"job_number": "M219 30B"})
    o = api.get(f"/api/customer-orders/{o['id']}")
    sh1 = make.ship(o, {o["lines"][0]["id"]: 4})
    sh2 = make.ship(api.get(f"/api/customer-orders/{o['id']}"), {o["lines"][0]["id"]: 6})
    assert sh1["code"].endswith("-M219-30B") and sh2["code"].endswith("-M219-30B") and sh1["code"] != sh2["code"]
    n1, n2 = (int(s["code"].split("-")[1]) for s in (sh1, sh2))          # SH-0001-M219-30B -> 1 (tests use SH-0001 numbering)
    assert n2 == n1 + 1

    r = client.get(f"/api/shipments/{sh1['id']}/packing-list.pdf", headers=admin_headers)
    assert f"{sh1['code']}-{o['po_number']}-Packing List.pdf" in r.headers["content-disposition"]
    link = client.get(f"/api/shipments/{sh1['id']}/packing-list.pdf?as_link=1", headers=admin_headers).json()
    assert link["name"] == f"{sh1['code']}-{o['po_number']}-Packing List.pdf"
    f = client.get(link["url"])                                         # no sign-in needed: the random id is the key
    assert f.status_code == 200 and f.content[:4] == b"%PDF" and "Packing%20List.pdf" in f.headers["content-disposition"]
    assert client.get(link["url"].replace(link["url"].split("/")[3], "nope")).status_code == 404

    api.put(f"/api/shipments/{sh1['id']}/code", json={"code": sh2["code"]}, expect=400)       # taken
    api.put(f"/api/shipments/{sh1['id']}/code", json={"code": "bad code"}, expect=400)          # no spaces
    assert api.put(f"/api/shipments/{sh1['id']}/code", json={"code": "SH-CUSTOM-1"})["code"] == "SH-CUSTOM-1"
    inv = make.invoice(api.get(f"/api/shipments/{sh2['id']}"))
    assert api.put(f"/api/invoices/{inv['id']}/code", json={"code": "INV-77"})["code"] == "INV-77"
    r = client.get(f"/api/invoices/{inv['id']}/pdf", headers=admin_headers)
    assert f"INV-77-{o['po_number']}-Invoice.pdf" in r.headers["content-disposition"]

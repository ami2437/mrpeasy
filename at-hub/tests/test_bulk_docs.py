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
    assert names == sorted([f"Packing-List-{sh1['code']}.pdf", f"Packing-List-{sh2['code']}.pdf", f"Labels-{sh1['code']}.pdf",
                            f"Labels-{sh2['code']}.pdf", f"{inv['code']}.pdf"])
    assert msg["To"] == "ap@example.com" and msg["Cc"] == "boss@example.com"
    assert api.get(f"/api/invoices/{inv['id']}")["status"] == "sent"            # sending an invoice makes it sent
    assert len(api.get(f"/api/shipments/{sh1['id']}/pod-emails")) == 1             # logged on the shipment


def test_documents_need_the_right_permission(client, admin_headers, make):
    from app.services.auth import AuthService
    client.post("/api/users/", json={"username": "drv-bulk", "password": "Str0ng!Passw0rd#", "role": "driver"}, headers=admin_headers)
    h = {"Authorization": f"Bearer {AuthService.create_access_token({'sub': 'drv-bulk'})}"}
    assert client.post("/api/bulk/documents/plan", json={"shipment_ids": [1], "kinds": ["packing_list"]}, headers=h).status_code == 403

"""Several sender addresses: the default per kind, the customer's own choice, picking one when sending; a password that
stops working (alert, email kept, sent again after the fix); passwords never shown; replies read off a mailbox onto
their record and attachments onto the AI Desk -- without marking anything read. A fake mail server stands in."""
import smtplib
from email.message import EmailMessage

import pytest

from tests.builders import uid


class FakeSMTP:
    sent, bad_passwords = [], set()

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        if password in FakeSMTP.bad_passwords:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def send_message(self, msg, to_addrs=None):
        FakeSMTP.sent.append((msg, list(to_addrs or [])))


class FakeIMAP:
    """A mailbox: messages by UID. Records how it was used (read-only select, PEEK fetches, appends)."""
    box, appended, selects, fetches = {}, [], [], []

    def __init__(self, *a, **k):
        pass

    def login(self, u, p):
        if p in FakeSMTP.bad_passwords:
            raise __import__("imaplib").IMAP4.error("bad")

    def select(self, folder, readonly=False):
        FakeIMAP.selects.append(readonly)
        return "OK", [b"1"]

    def response(self, name):
        return name, [b"77"]

    def uid(self, cmd, *args):
        if cmd == "search":
            return "OK", [" ".join(str(u) for u in sorted(FakeIMAP.box)).encode()]
        FakeIMAP.fetches.append(args[1])
        return "OK", [(b"x", FakeIMAP.box[int(args[0])])]

    def append(self, folder, flags, when, data):
        FakeIMAP.appended.append(folder)
        return "OK", [b""]

    def logout(self):
        pass


@pytest.fixture
def fake_mail(monkeypatch):
    from app.services import mailer
    FakeSMTP.sent.clear(), FakeSMTP.bad_passwords.clear(), FakeIMAP.box.clear(), FakeIMAP.appended.clear(), FakeIMAP.selects.clear(), FakeIMAP.fetches.clear()
    monkeypatch.setattr(mailer, "_connect", lambda *a, **k: FakeSMTP())
    monkeypatch.setattr(mailer.imaplib, "IMAP4_SSL", FakeIMAP)
    return FakeSMTP


def _sender(api, address, **kw):
    return api.post("/api/email/senders", json={"address": address, "display_name": address.split("@")[0], "login": "own",
                                               "smtp_host": "mail.example.com", "smtp_port": 465, "smtp_security": "ssl",
                                               "password": "secret", **kw})


def _invoice(make):
    a = make.item(price=2)
    make.stock(a, 10)
    o = make.order(lines=[(a, 5, 2.0)])
    return make.invoice(make.ship(o))


def test_from_address_default_choice_and_party(api, make, fake_mail):
    sfx = uid("")
    a, b = _sender(api, f"robert{sfx}@atind.test"), _sender(api, f"accounting{sfx}@atind.test", bcc_me=True)
    assert "password" not in a and a["has_password"]
    api.put("/api/email/defaults", json={"defaults": {"invoice": a["id"], "statement": b["id"]}})
    inv = _invoice(make)
    send = lambda **kw: api.post(f"/api/invoices/{inv['id']}/email", json={"to": "buyer@cust.test", "subject": "Invoice", "body": "Hi", "attach_pdf": False, **kw})
    send()
    assert fake_mail.sent[-1][0]["From"].endswith(f"<robert{sfx}@atind.test>")  # the invoice default
    send(from_id=b["id"])
    msg, rcpts = fake_mail.sent[-1]
    assert f"accounting{sfx}@atind.test" in msg["From"] and f"accounting{sfx}@atind.test" in rcpts  # picked + its BCC-me copy
    # the customer's own choice beats the default
    api.put(f"/api/customers/{inv['customer_id']}", json={"email_from_id": b["id"]})
    send()
    assert f"accounting{sfx}@atind.test" in fake_mail.sent[-1][0]["From"]
    ch = api.get(f"/api/email/choices?kind=invoice&party_type=customer&party_id={inv['customer_id']}")
    assert ch["default_id"] == b["id"]
    log = api.get("/api/email/log")["emails"]
    assert log[0]["record_code"] == inv["code"] and log[0]["status"] == "sent" and log[0]["kind"] == "invoice"
    assert "Sent" in "".join(FakeIMAP.appended)  # a copy in the mailbox's Sent folder


def test_password_stops_working_alert_keep_and_resend(api, make, client, admin_headers, fake_mail):
    s = _sender(api, f"inv{uid('')}@atind.test")
    inv = _invoice(make)
    fake_mail.bad_passwords.add("secret")  # changed on the mail server
    r = client.post(f"/api/invoices/{inv['id']}/email", headers=admin_headers,
                    json={"to": "buyer@cust.test", "subject": "Invoice", "body": "Hi", "attach_pdf": False, "from_id": s["id"]})
    assert r.status_code == 502 and "password" in r.json()["detail"]
    got = next(x for x in api.get("/api/email/senders")["senders"] if x["id"] == s["id"])
    assert got["status"] == "failing" and got["failing_since"]
    assert any(t["title"].startswith(f"Email for {s['address']}") and t["status"] == "open" for t in api.get("/api/tasks/"))
    assert any(f["id"] == s["id"] for f in api.get("/api/email/health")["failing"])
    failed = next(x for x in api.get("/api/email/log?status=failed")["emails"] if x["from"] == s["address"])
    # the new password: checked at once, alert closed, the kept email goes out
    fake_mail.bad_passwords.clear()
    ok = api.post(f"/api/email/senders/{s['id']}/password", json={"password": "new-one"})
    assert ok["ok"] and ok["sender"]["status"] == "ok"
    assert not any(t["title"].startswith(f"Email for {s['address']}") and t["status"] == "open" for t in api.get("/api/tasks/"))
    api.post(f"/api/email/log/{failed['id']}/resend")
    assert fake_mail.sent and fake_mail.sent[-1][0]["Subject"] == "Invoice"


def test_replies_land_on_their_record_and_files_on_the_desk(api, make, fake_mail):
    from app.config.database import SessionLocal
    from app.models import DeskFile, EmailSender
    from app.services import mailer
    s = _sender(api, f"box{uid('')}@atind.test", read_inbox=True)
    inv = _invoice(make)
    api.post(f"/api/invoices/{inv['id']}/email", json={"to": "buyer@cust.test", "subject": "Invoice", "body": "Hi", "attach_pdf": False, "from_id": s["id"]})
    our_id = fake_mail.sent[-1][0]["Message-ID"]
    db = SessionLocal()
    try:
        snd = db.get(EmailSender, s["id"])
        FakeIMAP.box[5] = b"old"
        assert mailer.poll(db, snd) == 0 and snd.imap_last_uid == 5  # the first look only starts the count
        reply = EmailMessage()
        reply["From"], reply["To"], reply["Subject"] = "buyer@cust.test", s["address"], "Re: Invoice"
        reply["Message-ID"], reply["In-Reply-To"] = "<r1@cust.test>", our_id
        reply.set_content("We paid this on the 5th.\n\nOn Mon someone wrote:\n> Hi")
        reply.add_attachment(b"%PDF-1.4 " + b"x" * 9000, maintype="application", subtype="pdf", filename="remittance.pdf")
        FakeIMAP.box[6] = bytes(reply)
        before = db.query(DeskFile).count()
        assert mailer.poll(db, snd) == 1
        assert db.query(DeskFile).count() == before + 1
    finally:
        db.close()
    th = api.get(f"/api/email/thread?record_type=invoice&record_id={inv['id']}")
    assert th["replies"][0]["snippet"].startswith("We paid this on the 5th") and "remittance.pdf" in th["replies"][0]["attachments"]
    assert all(FakeIMAP.selects) and all("PEEK" in f for f in FakeIMAP.fetches)  # read-only: nothing marked read

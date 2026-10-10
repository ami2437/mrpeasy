"""Who an email goes out from, how it gets there, and what's kept of it.

    choose(db, kind, sender_id, party)  -> the EmailSender to use (or None = the server's own SMTP_* from .env)
    deliver(db, msg, rcpts, sender, log) -> send; on a failed login the address is marked failing (admins alerted) and
                                           the message kept to send again; a copy goes to the mailbox's Sent folder
    check(db, sender)                   -> log in only (the daily check / "Test" button): ok, or why not
    poll_inboxes(db)                    -> new mail in the mailboxes AT-HUB reads: replies onto their records,
                                           attachments onto the AI Desk (never marks anything read, never deletes)

Kinds: invoice, statement (overdue reminders), credit_memo, purchase_order, quote, pod (proof of delivery), mtr."""
import email as email_lib
import imaplib
import json
import re
import smtplib
import ssl
import time as _time
import uuid
from datetime import datetime
from email import policy
from email.utils import parsedate_to_datetime, parseaddr
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models import AppSetting, EmailLog, EmailReply, EmailSender
from app.services.mail_secret import unseal

KINDS = {"invoice": "Invoices", "statement": "Statements & overdue reminders", "credit_memo": "Credit memos",
         "purchase_order": "Purchase orders to vendors", "quote": "Quotes", "pod": "Proof of delivery", "mtr": "MTRs"}
RECORD_PAGES = {"invoice": "invoices", "purchase_order": "purchase-orders", "quote": "quotes", "shipment": "shipments",
                "customer_order": "customer-orders", "customer": "customers"}


def defaults(db: Session) -> dict:
    row = db.get(AppSetting, "email_defaults")
    try:
        return json.loads(row.value) if row and row.value else {}
    except ValueError:
        return {}


def set_defaults(db: Session, d: dict, by: str) -> dict:
    row = db.get(AppSetting, "email_defaults") or AppSetting(key="email_defaults")
    row.value, row.updated_by = json.dumps({k: v for k, v in d.items() if k in KINDS and v}), by
    db.merge(row)
    db.commit()
    return defaults(db)


def _party_choice(party) -> Optional[int]:
    """The customer's / vendor's own "Send From" (on their card), if any."""
    try:
        return getattr(party, "email_from_id", None) or int((party.details or {}).get("email_from_id") or 0) or None
    except (TypeError, ValueError):
        return None


def choose(db: Session, kind: str, sender_id: Optional[int] = None, party=None) -> Optional[EmailSender]:
    """What you picked -> the customer's / vendor's own choice -> the default for this kind -> None (server login)."""
    for sid in (sender_id, _party_choice(party) if party is not None else None, defaults(db).get(kind)):
        if sid:
            s = db.get(EmailSender, int(sid))
            if s and s.active:
                return s
            if sender_id and sid == sender_id:
                raise HTTPException(status_code=400, detail="That From address isn't on the list (Company Settings -> Email)")
    return None


def from_of(sender: Optional[EmailSender]) -> str:
    return sender.address if sender else (settings.smtp_from or settings.smtp_username)


def ready(db: Session) -> bool:
    """Can AT-HUB send at all? (the server's own login, or at least one address with its own)."""
    if settings.smtp_host and (settings.smtp_from or settings.smtp_username):
        return True
    return any(s.login == "own" and s.smtp_host and s.password_enc for s in db.query(EmailSender).filter(EmailSender.active == True).all())  # noqa: E712


def _transport(sender: Optional[EmailSender]):
    """(host, port, security, username, password) for this sender."""
    if sender and sender.login == "own":
        if not sender.smtp_host or not sender.password_enc:
            raise HTTPException(status_code=400, detail=f"{sender.address} has no mail server / password yet -- Company Settings -> Email")
        return (sender.smtp_host, sender.smtp_port or (465 if sender.smtp_security == "ssl" else 587), sender.smtp_security or "starttls",
                sender.smtp_username or sender.address, unseal(sender.password_enc))
    if not settings.smtp_host:
        raise HTTPException(status_code=400, detail="Email isn't set up yet: give the address its own mailbox login in Company Settings -> Email, "
                                                    "or set the server's sending login (SMTP_* in .env)")
    return settings.smtp_host, settings.smtp_port, settings.smtp_security, settings.smtp_username, settings.smtp_password


def _connect(host, port, security, timeout=30):
    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
    if security == "starttls":
        server.starttls(context=ssl.create_default_context())
    return server


def _failing(db: Session, sender: Optional[EmailSender], why: str) -> None:
    """The address stopped working: say so on it, and put it in front of the admins (a task, until it works again)."""
    if not sender:
        return
    now = datetime.utcnow()
    if sender.status != "failing":
        sender.failing_since = now
    sender.status, sender.last_error, sender.last_check_at = "failing", why[:500], now
    from app.models import Task
    key = f"email-failing:{sender.id}"
    t = db.query(Task).filter(Task.key == key).first()
    if not t:
        db.add(Task(key=key, category="Email", title=f"Email for {sender.address} is failing", link="company.html#email",
                    detail=f"{why}\nSince {now:%Y-%m-%d %H:%M} UTC. Update its password (or mail server) in Company Settings -> Email; "
                           f"emails that failed meanwhile can be sent again from the Emails page.", created_by="system"))
    elif t.status != "open":
        t.status, t.detail = "open", f"{why}\nSince {now:%Y-%m-%d %H:%M} UTC."
    db.commit()


def _working(db: Session, sender: Optional[EmailSender]) -> None:
    if not sender:
        return
    sender.status, sender.last_error, sender.failing_since = "ok", None, None
    sender.last_ok_at = sender.last_check_at = datetime.utcnow()
    from app.models import Task
    t = db.query(Task).filter(Task.key == f"email-failing:{sender.id}", Task.status == "open").first()
    if t:
        t.status, t.done_by, t.done_at = "done", "system", datetime.utcnow()
    db.commit()


def _keep_raw(msg) -> str:
    from app.routes.attachments import upload_root
    folder = upload_root() / "mail-outbox"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"mail-outbox/{uuid.uuid4().hex[:16]}.eml"
    (upload_root() / name).write_bytes(bytes(msg))
    return name


def _save_to_sent(sender: EmailSender, msg) -> None:
    """A copy in the mailbox's Sent folder, so it shows in Outlook like any other sent mail."""
    if not (sender and sender.login == "own" and sender.save_sent and sender.password_enc):
        return
    host = sender.imap_host or sender.smtp_host
    try:
        im = imaplib.IMAP4_SSL(host, sender.imap_port or 993, timeout=20)
        im.login(sender.smtp_username or sender.address, unseal(sender.password_enc))
        folder = sender.sent_folder or "INBOX.Sent"
        for f in ([folder] if sender.sent_folder else ["INBOX.Sent", "Sent", "Sent Items", "Sent Messages"]):
            typ, _ = im.append(f'"{f}"', "\\Seen", imaplib.Time2Internaldate(_time.time()), bytes(msg))
            if typ == "OK":
                break
        im.logout()
    except Exception:
        pass  # the email went out; a missing Sent copy is not worth failing it


def deliver(db: Session, msg, rcpts: list, sender: Optional[EmailSender], log: EmailLog, bcc_me: bool = False) -> None:
    host, port, security, user, password = _transport(sender)
    all_rcpts = list(rcpts) + ([sender.address] if sender and bcc_me else [])
    try:
        with _connect(host, port, security) as server:
            if user:
                server.login(user, password)
            server.send_message(msg, to_addrs=all_rcpts)
    except smtplib.SMTPAuthenticationError:
        why = f"The mail server rejected the password for {user}"
        log.status, log.error, log.raw_path = "failed", why, _keep_raw(msg)
        db.add(log)
        db.commit()
        _failing(db, sender, why)
        raise HTTPException(status_code=502, detail=f"{why} -- update it in Company Settings -> Email (the email is kept; send it again from the Emails page)")
    except (smtplib.SMTPException, OSError) as e:
        why = f"Could not send email: {e}"
        log.status, log.error, log.raw_path = "failed", why[:500], _keep_raw(msg)
        db.add(log)
        db.commit()
        if sender and isinstance(e, OSError):
            _failing(db, sender, why)
        raise HTTPException(status_code=502, detail=why)
    log.status = "sent"
    db.add(log)
    db.commit()
    _working(db, sender)
    _save_to_sent(sender, msg)


def check(db: Session, sender: EmailSender) -> dict:
    """Log in only -- nothing is sent."""
    try:
        host, port, security, user, password = _transport(sender)
        with _connect(host, port, security, timeout=20) as server:
            if user:
                server.login(user, password)
        if sender.read_inbox and sender.login == "own":
            im = imaplib.IMAP4_SSL(sender.imap_host or sender.smtp_host, sender.imap_port or 993, timeout=20)
            im.login(sender.smtp_username or sender.address, unseal(sender.password_enc))
            im.logout()
        _working(db, sender)
        return {"ok": True}
    except smtplib.SMTPAuthenticationError:
        why = "The mail server rejected the password"
    except imaplib.IMAP4.error as e:
        why = f"Reading the inbox failed: {e}"
    except HTTPException as e:
        why = str(e.detail)
    except (smtplib.SMTPException, OSError) as e:
        why = f"Couldn't reach the mail server: {e}"
    _failing(db, sender, why)
    return {"ok": False, "error": why}


def check_all(db: Session) -> None:
    for s in db.query(EmailSender).filter(EmailSender.active == True).all():  # noqa: E712
        if s.login == "own" or settings.smtp_host:
            check(db, s)


def resend(db: Session, log_id: int, by: str) -> EmailLog:
    from app.routes.attachments import upload_root
    log = db.get(EmailLog, log_id)
    if not log or log.status != "failed" or not log.raw_path:
        raise HTTPException(status_code=400, detail="That email isn't waiting to be sent again")
    msg = email_lib.message_from_bytes((upload_root() / log.raw_path).read_bytes(), policy=policy.default)
    sender = db.get(EmailSender, log.sender_id) if log.sender_id else None
    rcpts = [a.strip() for a in ",".join(x for x in (log.to_address, log.cc_address) if x).split(",") if a.strip()]
    again = EmailLog(kind=log.kind, record_type=log.record_type, record_id=log.record_id, record_code=log.record_code, party=log.party,
                     sender_id=log.sender_id, from_address=log.from_address, to_address=log.to_address, cc_address=log.cc_address,
                     subject=log.subject, attachments=log.attachments, message_id=log.message_id, sent_by=by)
    deliver(db, msg, rcpts, sender, again, bcc_me=bool(sender and sender.bcc_me))
    log.status = "resent"
    db.commit()
    return again


# ---------------------------------------------------------------------------------------------------- the inbox
CODE_RE = re.compile(r"\b(Inv-\d{5,}|PO\d{4,}|C\d{5}|SH\d{5,}|Q\d{4,}|CM\d{3,})\b", re.I)


def _record_for(db: Session, subject: str, refs: list):
    """(log, record_type, record_id, code) the message is about: our thread first, then a number in the subject."""
    for r in refs:
        log = db.query(EmailLog).filter(EmailLog.message_id == r).first()
        if log:
            return log, log.record_type, log.record_id, log.record_code
    from app.models import CustomerOrder, Invoice, PurchaseOrder, Quote, Shipment
    for m in CODE_RE.finditer(subject or ""):
        code = m.group(1)
        for model, rtype in ((Invoice, "invoice"), (PurchaseOrder, "purchase_order"), (CustomerOrder, "customer_order"),
                             (Shipment, "shipment"), (Quote, "quote")):
            rec = db.query(model).filter(model.code.ilike(code)).first()
            if rec:
                return None, rtype, rec.id, rec.code
    return None, None, None, None


DOC_TYPES = (".pdf", ".xlsx", ".xls", ".csv", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff")


def _take(db: Session, sender: EmailSender, raw: bytes) -> None:
    msg = email_lib.message_from_bytes(raw, policy=policy.default)
    mid = (msg.get("Message-ID") or "").strip()
    if mid and db.query(EmailReply).filter(EmailReply.message_id == mid).first():
        return
    from_addr = parseaddr(msg.get("From") or "")[1]
    if from_addr.lower() == sender.address.lower():
        return  # our own (a copy we sent)
    refs = [x for x in re.split(r"\s+", f"{msg.get('In-Reply-To') or ''} {msg.get('References') or ''}") if x]
    subject = str(msg.get("Subject") or "")
    log, rtype, rid, code = _record_for(db, subject, refs)
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body and body.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    text = re.split(r"\n\s*(On .+ wrote:|-----Original Message-----|From: )", text)[0]
    files = []
    if sender.attachments_to_desk:
        from app.routes.attachments import upload_root
        from app.models import DeskFile
        from pathlib import Path
        for part in msg.iter_attachments():
            name = part.get_filename() or ""
            data = part.get_payload(decode=True) or b""
            if not name.lower().endswith(DOC_TYPES) or len(data) < 8000 and not name.lower().endswith((".pdf", ".xlsx", ".csv")):
                continue  # logos and signature pictures aren't documents
            ext = Path(name).suffix.lower()
            stored = f"desk/{uuid.uuid4().hex[:12]}{ext}"
            (upload_root() / "desk").mkdir(parents=True, exist_ok=True)
            (upload_root() / stored).write_bytes(data)
            db.add(DeskFile(filename=Path(name).name, stored_name=stored, content_type=part.get_content_type(), size=len(data),
                            status="new", uploaded_by=f"email:{sender.address}"))
            files.append(name)
    when = None
    try:
        when = parsedate_to_datetime(msg.get("Date")).replace(tzinfo=None) if msg.get("Date") else None
    except Exception:
        when = None
    if rtype or files:  # a reply about one of our records, or documents for the desk
        db.add(EmailReply(sender_id=sender.id, email_log_id=log.id if log else None, record_type=rtype, record_id=rid, record_code=code,
                          from_address=from_addr, subject=subject[:500], snippet=(text or "").strip()[:2000], message_id=mid or None,
                          attachments=", ".join(files) or None, received_at=when))
    db.commit()


def poll(db: Session, sender: EmailSender) -> int:
    """New messages since the last look (by UID; the first look only starts the count). Read with PEEK: nothing is
    marked read, moved or deleted."""
    im = imaplib.IMAP4_SSL(sender.imap_host or sender.smtp_host, sender.imap_port or 993, timeout=30)
    try:
        im.login(sender.smtp_username or sender.address, unseal(sender.password_enc))
        typ, _ = im.select(f'"{sender.inbox_folder or "INBOX"}"', readonly=True)
        if typ != "OK":
            raise imaplib.IMAP4.error(f"no folder {sender.inbox_folder}")
        uv = (im.response("UIDVALIDITY")[1] or [b""])[0]
        uv = uv.decode() if isinstance(uv, bytes) else str(uv or "")
        typ, data = im.uid("search", None, "ALL")
        uids = [int(x) for x in (data[0] or b"").split()] if typ == "OK" else []
        if sender.imap_uidvalidity != uv or sender.imap_last_uid is None:
            sender.imap_uidvalidity, sender.imap_last_uid = uv, max(uids or [0])  # start from now: old mail isn't pulled in
            db.commit()
            return 0
        new = [u for u in uids if u > (sender.imap_last_uid or 0)][:50]
        for u in new:
            typ, msgdata = im.uid("fetch", str(u), "(BODY.PEEK[])")
            if typ == "OK" and msgdata and isinstance(msgdata[0], tuple):
                try:
                    _take(db, sender, msgdata[0][1])
                except Exception:
                    db.rollback()
            sender.imap_last_uid = u
            db.commit()
        return len(new)
    finally:
        try:
            im.logout()
        except Exception:
            pass


def poll_inboxes(db: Session) -> None:
    for s in db.query(EmailSender).filter(EmailSender.active == True, EmailSender.read_inbox == True, EmailSender.login == "own").all():  # noqa: E712
        try:
            poll(db, s)
        except imaplib.IMAP4.error as e:
            _failing(db, s, f"Reading the inbox failed: {e}")
        except Exception:
            db.rollback()

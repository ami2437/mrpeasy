"""Email: the addresses AT-HUB sends from, the default per kind of email, health (daily login check, alerts), the
Emails page (everything sent, replies that came back) and sending a failed one again. services/mailer.py does the work."""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.config.settings import settings
from app.dependencies import get_current_active_user, require_any, require_perm
from app.models import EmailLog, EmailReply, EmailSender, User
from app.services import mailer
from app.services.mail_secret import seal

router = APIRouter(prefix="/api/email", tags=["email"])
admin = [Depends(require_perm("company"))]
RECORD_VIEW = [Depends(require_any("orders.view", "purchasing", "invoices", "shipments.view", "quotes"))]
FIELDS = ("address", "display_name", "reply_to", "signature", "bcc_me", "login", "smtp_host", "smtp_port", "smtp_security", "smtp_username",
          "imap_host", "imap_port", "save_sent", "sent_folder", "read_inbox", "inbox_folder", "attachments_to_desk", "active")


def _out(s: EmailSender) -> dict:
    o = {f: getattr(s, f) for f in FIELDS}
    o.update({"id": s.id, "has_password": bool(s.password_enc), "status": s.status, "last_error": s.last_error,
              "failing_since": s.failing_since, "last_ok_at": s.last_ok_at, "last_check_at": s.last_check_at})
    return o


class SenderIn(BaseModel):
    address: str
    display_name: Optional[str] = None
    reply_to: Optional[str] = None
    signature: Optional[str] = None
    bcc_me: bool = False
    login: str = "server"  # server | own
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_security: Optional[str] = "starttls"
    smtp_username: Optional[str] = None
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    save_sent: bool = True
    sent_folder: Optional[str] = None
    read_inbox: bool = False
    inbox_folder: Optional[str] = "INBOX"
    attachments_to_desk: bool = True
    active: bool = True
    password: Optional[str] = None  # only when set / changed; never read back


def _apply(db: Session, s: EmailSender, data: SenderIn) -> None:
    from app.services.email import EMAIL_RE
    d = data.dict()
    addr = (d["address"] or "").strip().lower()
    if not EMAIL_RE.match(addr):
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    other = db.query(EmailSender).filter(EmailSender.address == addr, EmailSender.id != (s.id or 0)).first()
    if other:
        raise HTTPException(status_code=400, detail=f"{addr} is on the list already")
    if d["login"] not in ("server", "own"):
        raise HTTPException(status_code=400, detail="Login is server or own")
    if d["reply_to"] and not EMAIL_RE.match(d["reply_to"].strip()):
        raise HTTPException(status_code=400, detail="Reply-to isn't a valid address")
    for f in FIELDS:
        v = d.get(f)
        setattr(s, f, v.strip() if isinstance(v, str) else v)
    s.address = addr
    if d.get("password"):
        s.password_enc = seal(d["password"])
        s.status, s.last_error = "unknown", None


@router.get("/senders", dependencies=admin)
def senders(db: Session = Depends(get_db)):
    return {"senders": [_out(s) for s in db.query(EmailSender).order_by(EmailSender.address).all()], "defaults": mailer.defaults(db),
            "kinds": mailer.KINDS, "server": {"host": settings.smtp_host or None, "from": settings.smtp_from or settings.smtp_username or None}}


@router.post("/senders", dependencies=admin)
def add_sender(data: SenderIn, db: Session = Depends(get_db)):
    s = EmailSender()
    _apply(db, s, data)
    db.add(s)
    db.commit()
    db.refresh(s)
    return _out(s)


@router.put("/senders/{sid}", dependencies=admin)
def edit_sender(sid: int, data: SenderIn, db: Session = Depends(get_db)):
    s = db.get(EmailSender, sid)
    if not s:
        raise HTTPException(status_code=404, detail="Not found")
    _apply(db, s, data)
    db.commit()
    return _out(s)


@router.delete("/senders/{sid}", dependencies=admin)
def delete_sender(sid: int, db: Session = Depends(get_db)):
    s = db.get(EmailSender, sid)
    if s:
        if db.query(EmailLog).filter(EmailLog.sender_id == sid).first():
            s.active = False  # its sent mail keeps pointing at it
        else:
            db.delete(s)
        d = {k: v for k, v in mailer.defaults(db).items() if v != sid}
        mailer.set_defaults(db, d, "system")
        db.commit()
    return {"ok": True}


class PasswordIn(BaseModel):
    password: str


@router.post("/senders/{sid}/password", dependencies=admin)
def set_password(sid: int, data: PasswordIn, db: Session = Depends(get_db)):
    """New password (e.g. changed on the mail server): saved encrypted, then checked straight away."""
    s = db.get(EmailSender, sid)
    if not s:
        raise HTTPException(status_code=404, detail="Not found")
    if not data.password:
        raise HTTPException(status_code=400, detail="Enter the password")
    s.password_enc = seal(data.password)
    db.commit()
    return {**mailer.check(db, s), "sender": _out(s)}


@router.post("/senders/{sid}/check", dependencies=admin)
def check_sender(sid: int, db: Session = Depends(get_db)):
    s = db.get(EmailSender, sid)
    if not s:
        raise HTTPException(status_code=404, detail="Not found")
    return {**mailer.check(db, s), "sender": _out(s)}


class TestIn(BaseModel):
    to: str


@router.post("/senders/{sid}/test", dependencies=admin)
def test_sender(sid: int, data: TestIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    from app.services import email as email_service
    s = db.get(EmailSender, sid)
    if not s:
        raise HTTPException(status_code=404, detail="Not found")
    email_service._send(db, data.to, "", "AT-HUB test email", f"This is a test from AT-HUB, sent from {s.address}.\n\nIf you can read this, it works.",
                        [("From", s.address)], None, kind="test", sender_id=s.id, sent_by=user.username)
    return {"ok": True}


class DefaultsIn(BaseModel):
    defaults: dict


@router.put("/defaults", dependencies=admin)
def put_defaults(data: DefaultsIn, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    clean = {}
    for k, v in (data.defaults or {}).items():
        if k in mailer.KINDS and v:
            if not db.get(EmailSender, int(v)):
                raise HTTPException(status_code=400, detail="Unknown address")
            clean[k] = int(v)
    return mailer.set_defaults(db, clean, user.username)


@router.get("/choices", dependencies=RECORD_VIEW)
def choices(kind: str = "", party_type: str = "", party_id: Optional[int] = None, db: Session = Depends(get_db)):
    """For an email form's From list: the active addresses, and the one picked for this kind / customer / vendor."""
    from app.models import Customer, Vendor
    party = None
    if party_id and party_type in ("customer", "vendor"):
        party = db.get(Customer if party_type == "customer" else Vendor, party_id)
    chosen = mailer.choose(db, kind, None, party)
    rows = [{"id": s.id, "address": s.address, "display_name": s.display_name, "status": s.status}
            for s in db.query(EmailSender).filter(EmailSender.active == True).order_by(EmailSender.address).all()]  # noqa: E712
    return {"senders": rows, "default_id": chosen.id if chosen else None, "server_from": settings.smtp_from or settings.smtp_username or None,
            "ready": mailer.ready(db)}


@router.get("/health")
def health(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    """Addresses that stopped working -- shown to the people who can fix them (Company Settings)."""
    from app.services.permissions import has
    if not has(user, "company"):
        return {"failing": []}
    return {"failing": [{"id": s.id, "address": s.address, "error": s.last_error, "since": s.failing_since}
                        for s in db.query(EmailSender).filter(EmailSender.active == True, EmailSender.status == "failing").all()]}  # noqa: E712


@router.get("/log", dependencies=[Depends(require_perm("emails"))])
def log(days: int = 90, q: str = "", status: str = "", kind: str = "", date_from: str = "", date_to: str = "", db: Session = Depends(get_db)):
    query = db.query(EmailLog)
    if date_from:
        query = query.filter(EmailLog.sent_at >= datetime.fromisoformat(date_from[:10]))
        if date_to:
            query = query.filter(EmailLog.sent_at < datetime.fromisoformat(date_to[:10]) + timedelta(days=1))
    else:
        query = query.filter(EmailLog.sent_at >= datetime.utcnow() - timedelta(days=max(1, min(days, 3650))))
    if status:
        query = query.filter(EmailLog.status == status)
    if kind:
        query = query.filter(EmailLog.kind == kind)
    rows = query.order_by(EmailLog.id.desc()).limit(1000).all()
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in " ".join(str(x or "") for x in (r.record_code, r.party, r.to_address, r.subject, r.from_address, r.sent_by)).lower()]
    replies = {}
    for rp in db.query(EmailReply).filter(EmailReply.email_log_id.in_([r.id for r in rows] or [0])).all():
        replies.setdefault(rp.email_log_id, []).append(rp)
    out = []
    for r in rows:
        out.append({"id": r.id, "kind": r.kind, "kind_label": mailer.KINDS.get(r.kind, r.kind or ""), "record_type": r.record_type, "record_id": r.record_id,
                    "record_code": r.record_code, "page": mailer.RECORD_PAGES.get(r.record_type), "party": r.party, "from": r.from_address,
                    "to": r.to_address, "cc": r.cc_address, "subject": r.subject, "attachments": r.attachments, "status": r.status, "error": r.error,
                    "sent_by": r.sent_by, "sent_at": r.sent_at,
                    "replies": [{"from": x.from_address, "at": x.received_at or x.created_at, "snippet": (x.snippet or "")[:300]} for x in replies.get(r.id, [])]})
    loose = db.query(EmailReply).filter(EmailReply.email_log_id.is_(None)).order_by(EmailReply.id.desc()).limit(200).all()
    return {"emails": out, "other_mail": [{"id": x.id, "from": x.from_address, "subject": x.subject, "at": x.received_at or x.created_at,
                                           "record_type": x.record_type, "record_id": x.record_id, "record_code": x.record_code,
                                           "page": mailer.RECORD_PAGES.get(x.record_type), "attachments": x.attachments,
                                           "snippet": (x.snippet or "")[:300]} for x in loose]}


@router.post("/log/{lid}/resend", dependencies=[Depends(require_perm("emails"))])
def resend(lid: int, db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    again = mailer.resend(db, lid, user.username)
    return {"ok": True, "id": again.id}


@router.get("/thread", dependencies=RECORD_VIEW)
def thread(record_type: str = Query(...), record_id: int = Query(...), db: Session = Depends(get_db)):
    """A record's emails: what was sent (from which address, delivered or failed) and the replies that came back."""
    sent = db.query(EmailLog).filter(EmailLog.record_type == record_type, EmailLog.record_id == record_id).order_by(EmailLog.id).all()
    replies = db.query(EmailReply).filter(EmailReply.record_type == record_type, EmailReply.record_id == record_id).order_by(EmailReply.id).all()
    return {"sent": [{"id": r.id, "from": r.from_address, "to": r.to_address, "subject": r.subject, "status": r.status, "error": r.error,
                      "at": r.sent_at, "by": r.sent_by} for r in sent],
            "replies": [{"id": x.id, "from": x.from_address, "subject": x.subject, "at": x.received_at or x.created_at, "snippet": x.snippet,
                         "attachments": x.attachments} for x in replies]}

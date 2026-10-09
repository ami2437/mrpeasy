import re
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.services.auth import AuthService
from app.schemas import LoginRequest, Token, UserResponse, PasswordChange
from app.dependencies import get_current_active_user
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

MIN_PASSWORD_LENGTH = 8


def check_password_strength(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters")


# Failed logins per username and per address (in memory): too many in 15 minutes and that one is locked out a while.
# An office shares one address, so it gets more room than a single username.
_FAILS: dict = {}
_LIMIT, _IP_LIMIT, _WINDOW = 10, 50, 15 * 60


def _too_many(*keys) -> bool:
    import time
    now = time.time()
    for k in keys:
        _FAILS[k] = [t for t in _FAILS.get(k, []) if now - t < _WINDOW]
    return any(len(_FAILS[k]) >= (_IP_LIMIT if k.startswith("ip:") else _LIMIT) for k in keys)


def _failed(*keys) -> None:
    import time
    for k in keys:
        _FAILS.setdefault(k, []).append(time.time())


@router.post("/login", response_model=Token)
def login(request: LoginRequest, http: Request, db: Session = Depends(get_db)):
    keys = ("u:" + (request.username or "").strip().lower(), "ip:" + (http.client.host if http.client else "?"))
    if _too_many(*keys):
        raise HTTPException(status_code=429, detail="Too many failed attempts -- wait 15 minutes and try again")
    user = AuthService.authenticate_user(db, request.username, request.password)
    if not user:
        _failed(*keys)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    if user.totp_enabled:
        from app.services import totp
        if not (request.code or "").strip():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="TOTP_REQUIRED")
        if not totp.verify(user.totp_secret, request.code):
            _failed(*keys)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="That code didn't match -- use the newest one in your authenticator app")
    _FAILS.pop(keys[0], None)
    user.last_login = datetime.utcnow()
    db.commit()
    db.refresh(user)
    token = AuthService.create_access_token({"sub": user.username})
    from app.services.permissions import KEYS, perms_for
    user.permissions = sorted(perms_for(db, user.role), key=KEYS.index)
    from app.services.permissions import role_name
    user.role_name = role_name(db, user.role)
    return {"access_token": token, "token_type": "bearer", "user": user}


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    from app.services.permissions import KEYS, role_name
    current_user.permissions = sorted(current_user.permissions, key=KEYS.index)
    current_user.role_name = role_name(db, current_user.role)
    return current_user


@router.post("/change-password", response_model=UserResponse)
def change_password(data: PasswordChange, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_active_user)):
    """Any user changing their own password (required after creation or a reset)."""
    user = _me(db, current_user)  # this session's copy: the signed-in user object comes detached (read-only lookup)
    if not AuthService.verify_password(data.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    check_password_strength(data.new_password)
    if data.new_password == data.current_password:
        raise HTTPException(status_code=400, detail="Choose a password different from the current one")
    user.hashed_password = AuthService.hash_password(data.new_password)
    user.must_change_password = False
    db.commit()
    return _me(db, current_user)


class TimezoneIn(BaseModel):
    timezone: Optional[str] = None  # "" / None = the company's


@router.put("/timezone", response_model=UserResponse)
def set_my_timezone(data: TimezoneIn, current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    """Each person's own time zone: every time on their screens is shown in it (like Outlook)."""
    from app.services.clock import check_zone
    from app.services.permissions import KEYS, role_name
    user = db.get(User, current_user.id)
    user.timezone = check_zone(data.timezone)
    db.commit()
    db.refresh(user)
    user.permissions = sorted(current_user.permissions, key=KEYS.index)
    user.role_name = role_name(db, user.role)
    return user



# ---- own preferences: Print Options, Recently Viewed, saved filters (each person's own, nothing shared) ----
@router.get("/print-options")
def my_print_options(current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    from app.services import print_options
    return print_options.all_for_user(db.get(User, current_user.id))


@router.put("/print-options/{doc_type}")
def save_my_print_options(doc_type: str, choices: dict, current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    from app.services import print_options
    if not re.fullmatch(r"[a-z_]{2,40}", doc_type):
        raise HTTPException(status_code=400, detail="Unknown document type")
    try:
        return print_options.save(db, db.get(User, current_user.id), doc_type, choices)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class RecentIn(BaseModel):
    kind: str   # order | po | invoice | shipment | item | quote | customer | vendor
    id: int
    label: str  # what to show: "C89124 · Hudson"
    url: str    # where it opens


RECENT_MAX = 12


@router.get("/recent")
def my_recent(current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    import json
    return json.loads(db.get(User, current_user.id).recent_json or "[]")


@router.post("/recent")
def add_recent(data: RecentIn, current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    """A record was opened: it goes to the top of this person's Recently Viewed (one entry per record)."""
    import json
    if not data.url.endswith(f"?id={data.id}") or "//" in data.url or not re.fullmatch(r"[a-z_-]+\.html\?id=\d+", data.url):
        raise HTTPException(status_code=400, detail="Not a record link")
    user = db.get(User, current_user.id)
    rows = [r for r in json.loads(user.recent_json or "[]") if not (r["kind"] == data.kind and r["id"] == data.id)]
    rows.insert(0, {"kind": data.kind[:20], "id": data.id, "label": data.label[:120], "url": data.url,
                    "at": datetime.utcnow().isoformat(timespec="seconds")})
    user.recent_json = json.dumps(rows[:RECENT_MAX])
    db.commit()
    return rows[:RECENT_MAX]


@router.get("/filters/{page}")
def my_filters(page: str, current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    import json
    return json.loads(db.get(User, current_user.id).filters_json or "{}").get(page, [])


@router.put("/filters/{page}")
def save_my_filters(page: str, rows: list = Body(...), current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    """This screen's saved filters, replaced as a whole: [{name, state}] (state = whatever the screen restores)."""
    import json
    if not re.fullmatch(r"[a-z_-]{2,40}", page):
        raise HTTPException(status_code=400, detail="Unknown screen")
    clean = [{"name": str(r.get("name") or "").strip()[:60], "state": r.get("state") or {}} for r in rows[:30]
             if isinstance(r, dict) and str(r.get("name") or "").strip()]
    if len(json.dumps(clean)) > 20000:
        raise HTTPException(status_code=400, detail="Too many saved filters")
    user = db.get(User, current_user.id)
    allf = json.loads(user.filters_json or "{}")
    allf[page] = clean
    user.filters_json = json.dumps(allf)
    db.commit()
    return clean


# ---- two-step login (authenticator app) ----
def _me(db: Session, current_user: User) -> User:
    from app.services.permissions import KEYS, role_name
    user = db.get(User, current_user.id)
    user.permissions = sorted(current_user.permissions, key=KEYS.index)
    user.role_name = role_name(db, user.role)
    return user


@router.post("/2fa/setup")
def totp_setup(current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    """A new key for the authenticator app (scan the QR). Not switched on until a code from the app is confirmed."""
    from app.services import totp
    user = db.get(User, current_user.id)
    if user.totp_enabled:
        raise HTTPException(status_code=400, detail="Two-step login is already on -- turn it off first to set up a new phone")
    user.totp_secret = totp.new_secret()
    db.commit()
    return {"secret": user.totp_secret, "qr_svg": totp.qr_svg(totp.uri(user.totp_secret, user.username))}


class TotpCode(BaseModel):
    code: str


@router.post("/2fa/enable", response_model=UserResponse)
def totp_enable(data: TotpCode, current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    from app.services import totp
    user = _me(db, current_user)
    if not user.totp_secret or not totp.verify(user.totp_secret, data.code):
        raise HTTPException(status_code=400, detail="That code didn't match -- type the 6 digits the app shows now")
    user.totp_enabled = True
    db.commit()
    db.refresh(user)
    return _me(db, current_user)


class TotpOff(BaseModel):
    password: str


@router.post("/2fa/disable", response_model=UserResponse)
def totp_disable(data: TotpOff, current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    user = db.get(User, current_user.id)
    if not AuthService.verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Password is incorrect")
    user.totp_enabled, user.totp_secret = False, None
    db.commit()
    return _me(db, current_user)

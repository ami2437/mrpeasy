from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
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
    if not AuthService.verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    check_password_strength(data.new_password)
    if data.new_password == data.current_password:
        raise HTTPException(status_code=400, detail="Choose a password different from the current one")
    current_user.hashed_password = AuthService.hash_password(data.new_password)
    current_user.must_change_password = False
    db.commit()
    db.refresh(current_user)
    return current_user


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

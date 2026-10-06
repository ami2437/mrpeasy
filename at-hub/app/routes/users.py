"""Account management (the "HR screen"): super admins create accounts, assign roles,
reset passwords and deactivate users. Same rules as the main portal: there must always
be at least one active super admin."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.services.auth import AuthService
from app.schemas import UserResponse, UserCreate, UserUpdate, PasswordReset
from app.dependencies import require_perm, require_any
from app.models import Role, User
from app.routes.auth import check_password_strength

router = APIRouter(prefix="/api/users", tags=["users"])
super_admin = require_perm("users")


def _get(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _ensure_super_admin_remains(db: Session, user: User, next_role: Optional[str] = None, next_active: Optional[bool] = None):
    removes_access = (next_role is not None and next_role != "super_admin") or next_active is False
    if user.role != "super_admin" or not user.is_active or not removes_access:
        return
    remaining = db.query(User).filter(User.role == "super_admin", User.is_active.is_(True), User.id != user.id).count()
    if remaining == 0:
        raise HTTPException(status_code=400, detail="At least one active super admin account is required")


@router.get("/", response_model=list[UserResponse])
def list_users(db: Session = Depends(get_db), _: User = Depends(super_admin)):
    return db.query(User).order_by(User.is_active.desc(), User.username).all()


@router.post("/", response_model=UserResponse)
def create_user(data: UserCreate, db: Session = Depends(get_db), current: User = Depends(super_admin)):
    username = (data.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    if not db.get(Role, data.role):
        raise HTTPException(status_code=400, detail="Invalid role")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail=f"Username {username} is already taken")
    check_password_strength(data.password)
    user = User(
        username=username,
        full_name=data.full_name,
        email=data.email,
        role=data.role,
        hashed_password=AuthService.hash_password(data.password),
        must_change_password=True,
        is_active=True,
        created_by=current.username,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.put("/{user_id}", response_model=UserResponse)
def update_user(user_id: int, data: UserUpdate, db: Session = Depends(get_db), current: User = Depends(super_admin)):
    user = _get(db, user_id)
    if data.role is not None:
        if not db.get(Role, data.role):
            raise HTTPException(status_code=400, detail="Invalid role")
        _ensure_super_admin_remains(db, user, next_role=data.role)
        user.role = data.role
    if data.is_active is not None:
        if user.id == current.id and data.is_active is False:
            raise HTTPException(status_code=400, detail="You can't deactivate your own account")
        _ensure_super_admin_remains(db, user, next_active=data.is_active)
        user.is_active = data.is_active
    if data.full_name is not None:
        user.full_name = data.full_name
    if data.email is not None:
        user.email = data.email
    if data.timezone is not None:
        from app.services.clock import check_zone
        user.timezone = check_zone(data.timezone)
    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/reset-2fa", response_model=UserResponse)
def reset_2fa(user_id: int, db: Session = Depends(get_db), _: User = Depends(super_admin)):
    """Turn two-step login off for someone who lost their phone; they can set it up again on My Account."""
    user = _get(db, user_id)
    user.totp_enabled, user.totp_secret = False, None
    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/reset-password", response_model=UserResponse)
def reset_password(user_id: int, data: PasswordReset, db: Session = Depends(get_db), _: User = Depends(super_admin)):
    """Set a temporary password; the user has to choose a new one at next login."""
    user = _get(db, user_id)
    check_password_strength(data.password)
    user.hashed_password = AuthService.hash_password(data.password)
    user.must_change_password = True
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db), current: User = Depends(super_admin)):
    user = _get(db, user_id)
    if user.id == current.id:
        raise HTTPException(status_code=400, detail="You can't delete your own account")
    _ensure_super_admin_remains(db, user, next_active=False)
    db.delete(user)
    db.commit()
    return Response(status_code=204)


# ---- roles: named sets of permissions (app/services/permissions.py) ----
import json
import re
from datetime import datetime
from typing import List

from pydantic import BaseModel

from app.dependencies import get_current_active_user
from app.services import permissions as P

roles_router = APIRouter(prefix="/api/roles", tags=["roles"])


def _role_out(db: Session, r: Role) -> dict:
    perms = sorted(P.perms_for(db, r.key), key=P.KEYS.index)
    return {"key": r.key, "name": r.name, "description": r.description, "builtin": bool(r.builtin), "locked": r.key == "super_admin",
            "permissions": perms, "money": [p for p in perms if p in P.MONEY],
            "users": db.query(User).filter(User.role == r.key).count(), "updated_by": r.updated_by}


@roles_router.get("/catalog")
def catalog(_: User = Depends(get_current_active_user)):
    """Every permission, by module -- the grid on the Roles screen."""
    return [{"key": k, "module": m, "label": l, "money": money} for k, m, l, money, _lowest in P.CATALOG]


@roles_router.get("/")
def list_roles(db: Session = Depends(get_db), _: User = Depends(get_current_active_user)):
    """Everyone can read the role names (shown on accounts); only Users & Roles can change them."""
    order = {"super_admin": 0, "admin": 1, "manager": 2, "employee": 3}
    return [_role_out(db, r) for r in sorted(db.query(Role).all(), key=lambda r: (order.get(r.key, 9), r.name.lower()))]


class RoleIn(BaseModel):
    name: str
    description: Optional[str] = None
    permissions: List[str] = []


def _clean_perms(perms):
    bad = [p for p in perms if p not in P.KEYS]
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown permission(s): {', '.join(bad)}")
    return json.dumps([k for k in P.KEYS if k in set(perms)])


@roles_router.post("/")
def create_role(data: RoleIn, db: Session = Depends(get_db), current: User = Depends(super_admin)):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Give the role a name")
    if db.query(Role).filter(Role.name.ilike(name)).first():
        raise HTTPException(status_code=400, detail=f"There's already a role called {name}")
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "role"
    key, n = base, 2
    while db.get(Role, key):
        key, n = f"{base}_{n}", n + 1
    r = Role(key=key, name=name, description=(data.description or "").strip() or None, permissions=_clean_perms(data.permissions),
             builtin=False, updated_by=current.username)
    db.add(r)
    db.commit()
    return _role_out(db, r)


@roles_router.put("/{key}")
def update_role(key: str, data: RoleIn, db: Session = Depends(get_db), current: User = Depends(super_admin)):
    r = db.get(Role, key)
    if not r:
        raise HTTPException(status_code=404, detail="Role not found")
    if r.key == "super_admin":
        raise HTTPException(status_code=400, detail="Super admin always has everything, so someone can always manage users")
    if data.name.strip() and data.name.strip().lower() != r.name.lower() and db.query(Role).filter(Role.name.ilike(data.name.strip())).first():
        raise HTTPException(status_code=400, detail=f"There's already a role called {data.name.strip()}")
    r.name = data.name.strip() or r.name
    r.description = (data.description or "").strip() or None
    r.permissions = _clean_perms(data.permissions)
    r.updated_by, r.updated_at = current.username, datetime.utcnow()
    db.commit()
    return _role_out(db, r)


@roles_router.delete("/{key}", status_code=204)
def delete_role(key: str, db: Session = Depends(get_db), _: User = Depends(super_admin)):
    r = db.get(Role, key)
    if not r:
        raise HTTPException(status_code=404, detail="Role not found")
    if r.builtin:
        raise HTTPException(status_code=400, detail="Built-in roles can be changed but not deleted")
    n = db.query(User).filter(User.role == key).count()
    if n:
        raise HTTPException(status_code=400, detail=f"{n} user(s) still have this role -- give them another role first")
    db.delete(r)
    db.commit()
    return Response(status_code=204)


# ---- View as: see AT-HUB exactly as someone else does (read-only; app/main.py refuses changes) ----
@router.post("/{user_id}/view-as")
def view_as(user_id: int, db: Session = Depends(get_db), current: User = Depends(super_admin)):
    from datetime import timedelta
    from app.services.permissions import KEYS, perms_for, role_name
    target = _get(db, user_id)
    if not target.is_active:
        raise HTTPException(status_code=400, detail="That account is deactivated")
    if target.id == current.id:
        raise HTTPException(status_code=400, detail="That's you")
    token = AuthService.create_access_token({"sub": target.username, "view_as_by": current.username}, expires_delta=timedelta(hours=2))
    out = UserResponse.model_validate(target).model_dump()
    out.update(permissions=sorted(perms_for(db, target.role), key=KEYS.index), role_name=role_name(db, target.role), view_as_by=current.username)
    return {"access_token": token, "user": out}

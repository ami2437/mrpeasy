"""Account management (the "HR screen"): super admins create accounts, assign roles,
reset passwords and deactivate users. Same rules as the main portal: there must always
be at least one active super admin."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.services.auth import AuthService
from app.schemas import ROLES, UserResponse, UserCreate, UserUpdate, PasswordReset
from app.dependencies import require_role
from app.models import User
from app.routes.auth import check_password_strength

router = APIRouter(prefix="/api/users", tags=["users"])
super_admin = require_role("super_admin")


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
    if data.role not in ROLES:
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
        if data.role not in ROLES:
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

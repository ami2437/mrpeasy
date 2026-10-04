from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.services.auth import AuthService
from app.models import User

security = HTTPBearer()

# The built-in roles' old order -- only used to seed their default permissions now.
ROLE_RANK = {"employee": 1, "manager": 2, "admin": 3, "super_admin": 4}


async def get_current_active_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """The signed-in user, looked up in a short read-only session of its own -- so it never holds the write
    lock a change request takes (see app/services/concurrency.py)."""
    from app.config.database import SessionLocal
    from app.services.concurrency import READ_ONLY
    payload = AuthService.decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    token = READ_ONLY.set(True)
    db = SessionLocal()
    try:
        user = AuthService.get_user_by_username(db, payload.get("sub"))
        if user is not None:
            from app.services.permissions import perms_for
            perms = perms_for(db, user.role)
            db.expunge(user)  # its loaded fields stay usable after the session closes
            user.permissions = perms
    finally:
        db.close()
        READ_ONLY.reset(token)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def require_role(minimum: str):
    """Dependency: the current user must hold `minimum` or a higher role."""
    async def checker(user: User = Depends(get_current_active_user)) -> User:
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[minimum]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"This needs the {minimum.replace('_', ' ')} role or higher")
        return user
    return checker


def require_perm(perm: str):
    """Dependency: the current user's role has this permission (app/services/permissions.py)."""
    async def checker(user: User = Depends(get_current_active_user)) -> User:
        if perm not in user.permissions:
            from app.services.permissions import CATALOG
            label = next((c[2] for c in CATALOG if c[0] == perm), perm)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Your role doesn't include: {label}")
        return user
    return checker


def require_any(*perms: str):
    """Dependency: at least one of these permissions -- for data several screens share (a customer list is needed
    by orders, shipments and invoices alike)."""
    async def checker(user: User = Depends(get_current_active_user)) -> User:
        if not any(p in user.permissions for p in perms):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your role can't see this")
        return user
    return checker

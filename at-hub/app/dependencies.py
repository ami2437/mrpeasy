from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.config.database import get_db
from app.services.auth import AuthService
from app.models import User

security = HTTPBearer()

# Higher number = more access. Each role can do everything the roles below it can.
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
            db.expunge(user)  # its loaded fields stay usable after the session closes
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

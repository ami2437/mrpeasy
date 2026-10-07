from datetime import datetime, timedelta
from typing import Optional
from passlib.context import CryptContext
from jose import JWTError, jwt
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.config.settings import settings
from app.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def seed_admin_user(db: Session) -> None:
    """Create the initial admin user on first startup if no users exist yet."""
    if db.query(User).first():
        return
    if not settings.admin_password:
        return
    db.add(User(
        username=settings.admin_username,
        hashed_password=AuthService.hash_password(settings.admin_password),
        full_name="Administrator",
        role="super_admin",
        is_active=True,
    ))
    db.commit()


def ensure_super_admin(db: Session) -> None:
    """Accounts predate roles (everyone was "admin"): if nobody is a super admin yet,
    promote the configured admin account (or the oldest active admin) so someone can manage users."""
    if db.query(User).filter(User.role == "super_admin", User.is_active.is_(True)).first():
        return
    user = (db.query(User).filter(User.username == settings.admin_username, User.is_active.is_(True)).first()
            or db.query(User).filter(User.role == "admin", User.is_active.is_(True)).order_by(User.id).first())
    if user:
        user.role = "super_admin"
        db.commit()


class AuthService:
    @staticmethod
    def hash_password(password: str) -> str:
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        return pwd_context.verify(plain_password, hashed_password)

    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
        to_encode = data.copy()
        expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
        to_encode.update({"exp": expire})
        return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)

    @staticmethod
    def decode_token(token: str) -> Optional[dict]:
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
            if payload.get("sub") is None:
                return None
            return payload
        except JWTError:
            return None

    @staticmethod
    def get_user_by_username(db: Session, username: str) -> Optional[User]:
        """Usernames ignore case: "jasonb" signs in as "Jasonb" (no two accounts differ only by case)."""
        name = (username or "").strip()
        return (db.query(User).filter(User.username == name).first()
                or db.query(User).filter(func.lower(User.username) == name.lower()).first())

    @staticmethod
    def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
        user = AuthService.get_user_by_username(db, username)
        if not user or not user.is_active:
            return None
        if not AuthService.verify_password(password, user.hashed_password):
            return None
        return user

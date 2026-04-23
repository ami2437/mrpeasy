from datetime import datetime, timedelta
from typing import Optional
from passlib.context import CryptContext
from jose import JWTError, jwt
from app.config.settings import settings
from sqlalchemy.orm import Session
from app.models import User

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:
    """Service for handling authentication and JWT tokens."""
    
    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt."""
        return pwd_context.hash(password)
    
    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a plain password against a hashed password."""
        return pwd_context.verify(plain_password, hashed_password)
    
    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """Create a JWT access token."""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(
                minutes=settings.access_token_expire_minutes
            )
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(
            to_encode, settings.secret_key, algorithm=settings.algorithm
        )
        return encoded_jwt
    
    @staticmethod
    def decode_token(token: str) -> Optional[dict]:
        """Decode a JWT token and return the payload."""
        try:
            payload = jwt.decode(
                token, settings.secret_key, algorithms=[settings.algorithm]
            )
            username: str = payload.get("sub")
            if username is None:
                return None
            return payload
        except JWTError:
            return None
    
    @staticmethod
    def get_user_by_username(db: Session, username: str) -> Optional[User]:
        """Get a user by username from the database."""
        return db.query(User).filter(User.username == username).first()
    
    @staticmethod
    def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
        """Authenticate a user by username and password."""
        user = AuthService.get_user_by_username(db, username)
        if not user:
            return None
        if not AuthService.verify_password(password, user.hashed_password):
            return None
        return user
    
    @staticmethod
    def create_user(
        db: Session,
        username: str,
        email: str,
        password: str,
        full_name: Optional[str] = None,
        role: str = "viewer"
    ) -> User:
        """Create a new user in the database."""
        hashed_password = AuthService.hash_password(password)
        db_user = User(
            username=username,
            email=email,
            hashed_password=hashed_password,
            full_name=full_name,
            role=role,
            is_active=True
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user
    
    @staticmethod
    def user_has_role(user: User, required_roles: list[str]) -> bool:
        """Check if user has one of the required roles."""
        if not isinstance(required_roles, list):
            required_roles = [required_roles]
        return user.role in required_roles
    
    @staticmethod
    def is_owner(user: User) -> bool:
        """Check if user has owner role."""
        return user.role == "owner"
    
    @staticmethod
    def is_admin(user: User) -> bool:
        """Check if user has admin or owner role."""
        return user.role in ["owner", "admin"]
    
    @staticmethod
    def is_editor(user: User) -> bool:
        """Check if user is owner, admin or editor."""
        return user.role in ["owner", "admin", "editor"]


class RBACService:
    """Service for managing role-based access control."""
    
    # Define role permissions
    PERMISSIONS = {
        "owner": {
            "read": True,
            "write": True,
            "delete": True,
            "sync": True,
            "manage_users": True,
            "manage_roles": True,
            "full_access": True
        },
        "admin": {
            "read": True,
            "write": True,
            "delete": True,
            "sync": True,
            "manage_users": True,
            "manage_roles": False,
            "full_access": False
        },
        "editor": {
            "read": True,
            "write": True,
            "delete": False,
            "sync": True,
            "manage_users": False,
            "manage_roles": False,
            "full_access": False
        },
        "viewer": {
            "read": True,
            "write": False,
            "delete": False,
            "sync": False,
            "manage_users": False,
            "manage_roles": False,
            "full_access": False
        }
    }
    
    @staticmethod
    def can_perform_action(user: User, action: str) -> bool:
        """Check if user can perform a specific action based on their role."""
        if user.role not in RBACService.PERMISSIONS:
            return False
        return RBACService.PERMISSIONS[user.role].get(action, False)
    
    @staticmethod
    def get_user_permissions(user: User) -> dict:
        """Get all permissions for a user based on their role."""
        if user.role not in RBACService.PERMISSIONS:
            return {}
        return RBACService.PERMISSIONS[user.role]
    
    @staticmethod
    def require_read_access(user: User) -> bool:
        """Check if user has read access."""
        return RBACService.can_perform_action(user, "read")
    
    @staticmethod
    def require_write_access(user: User) -> bool:
        """Check if user has write access."""
        return RBACService.can_perform_action(user, "write")
    
    @staticmethod
    def require_delete_access(user: User) -> bool:
        """Check if user has delete access."""
        return RBACService.can_perform_action(user, "delete")
    
    @staticmethod
    def require_sync_access(user: User) -> bool:
        """Check if user has sync access."""
        return RBACService.can_perform_action(user, "sync")
    
    @staticmethod
    def require_admin(user: User) -> bool:
        """Check if user is admin or owner."""
        return user.role in ["owner", "admin"]

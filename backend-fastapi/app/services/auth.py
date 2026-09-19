from datetime import datetime, timedelta
from typing import Optional
from passlib.context import CryptContext
from jose import JWTError, jwt
from app.config.settings import settings
from sqlalchemy.orm import Session
from app.models import Role, User


VALID_ROLES = {"employee", "admin", "super_admin"}


def initialize_auth_roles(db: Session) -> None:
    """Seed requested roles and migrate legacy role names without changing credentials."""
    role_descriptions = {
        "employee": "Batch Labels and Shipments access",
        "admin": "All operational modules except Auth",
        "super_admin": "Unrestricted access including account management",
    }
    legacy_roles = {
        "owner": "super_admin",
        "editor": "employee",
        "viewer": "employee",
    }
    for role_name, description in role_descriptions.items():
        if not db.query(Role).filter(Role.name == role_name).first():
            db.add(Role(name=role_name, description=description))
    for legacy_role, new_role in legacy_roles.items():
        db.query(User).filter(User.role == legacy_role).update({User.role: new_role})
    db.commit()

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
        role: str = "employee"
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
        """Legacy alias for super-admin checks."""
        return user.role == "super_admin"
    
    @staticmethod
    def is_admin(user: User) -> bool:
        """Check if user has admin-level module access."""
        return user.role in ["super_admin", "admin"]
    
    @staticmethod
    def is_editor(user: User) -> bool:
        """Legacy alias for authenticated operational access."""
        return user.role in ["super_admin", "admin", "employee"]


class RBACService:
    """Service for managing role-based access control."""
    
    # Define role permissions
    PERMISSIONS = {
        "super_admin": {
            "read": True,
            "write": True,
            "delete": True,
            "sync": True,
            "manage_users": True,
            "auth": True,
            "batch_labels": True,
            "shipments": True,
            "invoicing": True,
            "customer_orders": True,
            "reports": True,
            "admin_ops": True,
            "full_access": True,
        },
        "admin": {
            "read": True,
            "write": True,
            "delete": True,
            "sync": True,
            "manage_users": False,
            "auth": False,
            "batch_labels": True,
            "shipments": True,
            "invoicing": True,
            "customer_orders": True,
            "reports": True,
            "admin_ops": True,
            "full_access": False,
        },
        "employee": {
            "read": True,
            "write": True,
            "delete": False,
            "sync": False,
            "manage_users": False,
            "auth": False,
            "batch_labels": True,
            "shipments": True,
            "invoicing": False,
            "customer_orders": False,
            "reports": False,
            "admin_ops": False,
            "full_access": False,
        },
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
        """Check if user has admin-level access."""
        return user.role in ["super_admin", "admin"]

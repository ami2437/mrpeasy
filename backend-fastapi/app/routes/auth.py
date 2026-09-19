from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Body
from sqlalchemy.orm import Session
from datetime import timedelta
from pydantic import BaseModel
from typing import Optional

from app.config.database import get_db
from app.schemas import UserCreate, UserPasswordReset, UserResponse, Token, UserUpdate
from app.services.auth import AuthService
from app.services.sync_service import SyncService
from app.models import User
from app.dependencies import get_current_user, get_current_active_user, require_role

# Login schema
class LoginRequest(BaseModel):
    username: str
    password: str

router = APIRouter(prefix="/api/auth", tags=["auth"])

VALID_ROLES = {"employee", "admin", "super_admin"}


def _ensure_super_admin_remains(db: Session, user: User, next_role: Optional[str] = None, next_active: Optional[bool] = None):
    removes_access = (next_role is not None and next_role != "super_admin") or next_active is False
    if user.role != "super_admin" or not user.is_active or not removes_access:
        return
    active_super_admins = db.query(User).filter(User.role == "super_admin", User.is_active.is_(True)).count()
    if active_super_admins <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one active super admin account is required"
        )


@router.post("/register", response_model=UserResponse)
async def register(
    user_data: UserCreate,
    current_user: User = Depends(require_role("super_admin")),
    db: Session = Depends(get_db)
):
    """
    Create a user account. Super admin only.
    
    - **username**: Unique username (required)
    - **email**: User email (required)
    - **password**: User password (required)
    - **full_name**: User full name (optional)
    - **role**: employee, admin, or super_admin
    """
    # Check if user already exists
    existing_user = AuthService.get_user_by_username(db, user_data.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    # Check if email already exists
    existing_email = db.query(User).filter(User.email == user_data.email).first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create user with role validation
    if user_data.role not in VALID_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role")
    
    user = AuthService.create_user(
        db=db,
        username=user_data.username,
        email=user_data.email,
        password=user_data.password,
        full_name=user_data.full_name,
        role=user_data.role
    )
    
    return user


@router.post("/login", response_model=Token)
async def login(
    login_data: Optional[LoginRequest] = Body(None),
    username: Optional[str] = None,
    password: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Login with username and password.
    Returns a JWT token for authenticated requests.
    
    Supports two request formats:
    1. JSON body: {"username": "...", "password": "..."}
    2. Query parameters: ?username=...&password=...
    """
    # Handle both JSON body and query parameters
    if login_data:
        _username = login_data.username
        _password = login_data.password
    elif username and password:
        _username = username
        _password = password
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username and password are required"
        )
    
    user = AuthService.authenticate_user(db, _username, _password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not active"
        )
    
    access_token_expires = timedelta(minutes=60)  # Shorter for login
    access_token = AuthService.create_access_token(
        data={"sub": user.username},
        expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }


@router.post("/login-with-sync", response_model=Token)
async def login_with_sync(
    username: str,
    password: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Login with username and password, then sync MRPeasy data in background.
    Returns a JWT token immediately, sync happens asynchronously.
    
    - **username**: User username (required)
    - **password**: User password (required)
    """
    user = AuthService.authenticate_user(db, username, password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not active"
        )
    
    access_token_expires = timedelta(minutes=60)
    access_token = AuthService.create_access_token(
        data={"sub": user.username},
        expires_delta=access_token_expires
    )
    
    # Trigger background sync if user has sync permission
    if user.role in ["super_admin", "admin"]:
        background_tasks.add_task(sync_all_data, db)
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user,
        "sync_status": "started" if user.role in ["super_admin", "admin"] else "skipped"
    }


def sync_all_data(db: Session):
    """Background task to sync all MRPeasy data"""
    try:
        SyncService.sync_customer_orders(db)
        SyncService.sync_stock_items(db)
        SyncService.sync_manufacturing_orders(db)
    except Exception as e:
        # Log error but don't fail login
        print(f"Background sync failed: {e}")


@router.post("/login-form")
async def login_form(
    username: str,
    password: str,
    db: Session = Depends(get_db)
):
    """
    Login endpoint for form data (application/x-www-form-urlencoded).
    Returns JWT token for authenticated requests.
    """
    user = AuthService.authenticate_user(db, username, password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not active"
        )
    
    access_token_expires = timedelta(minutes=60)
    access_token = AuthService.create_access_token(
        data={"sub": user.username},
        expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_active_user)
):
    """
    Get current authenticated user information.
    Requires valid JWT token in Authorization header.
    """
    return current_user


@router.put("/me", response_model=UserResponse)
async def update_current_user(
    user_update: UserUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Update current user information.
    Requires valid JWT token in Authorization header.
    
    - **email**: Update email (optional)
    - **full_name**: Update full name (optional)
    - **role**: Update role - only admin can change role (optional)
    """
    if user_update.username is not None:
        existing = db.query(User).filter(
            User.username == user_update.username,
            User.id != user_id
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already in use"
            )
        if not user_update.username.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username cannot be empty"
            )
        user.username = user_update.username.strip()

    if user_update.email:
        # Check if email already exists
        existing = db.query(User).filter(
            User.email == user_update.email,
            User.id != current_user.id
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use"
            )
        current_user.email = user_update.email
    
    if user_update.full_name is not None:
        current_user.full_name = user_update.full_name
    
    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    current_user: User = Depends(require_role("super_admin")),
    db: Session = Depends(get_db)
):
    """
    List all users.
    Requires admin role.
    """
    users = db.query(User).all()
    return users


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    current_user: User = Depends(require_role("super_admin")),
    db: Session = Depends(get_db)
):
    """
    Get a specific user by ID.
    Users can only view their own profile unless they are admin.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    return user


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    current_user: User = Depends(require_role("super_admin")),
    db: Session = Depends(get_db)
):
    """
    Update a user (admin only).
    Only admin users can update other users.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    if user_update.email:
        existing = db.query(User).filter(
            User.email == user_update.email,
            User.id != user_id
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use"
            )
        user.email = user_update.email
    
    if user_update.full_name is not None:
        user.full_name = user_update.full_name
    
    if user_update.role is not None:
        if user_update.role not in VALID_ROLES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role")
        _ensure_super_admin_remains(db, user, next_role=user_update.role)
        user.role = user_update.role

    if user_update.is_active is not None:
        if user.id == current_user.id and not user_update.is_active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot deactivate your own account")
        _ensure_super_admin_remains(db, user, next_active=user_update.is_active)
        user.is_active = user_update.is_active
    
    db.commit()
    db.refresh(user)
    return user


@router.put("/users/{user_id}/password")
async def reset_user_password(
    user_id: int,
    password_reset: UserPasswordReset,
    current_user: User = Depends(require_role("super_admin")),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.hashed_password = AuthService.hash_password(password_reset.password)
    db.commit()
    return {"message": "Password updated successfully"}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    current_user: User = Depends(require_role("super_admin")),
    db: Session = Depends(get_db)
):
    """
    Delete a user (admin only).
    Cannot delete your own account.
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    _ensure_super_admin_remains(db, user, next_active=False)

    db.delete(user)
    db.commit()
    return {"message": "User deleted successfully"}

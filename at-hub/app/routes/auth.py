from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
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


@router.post("/login", response_model=Token)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = AuthService.authenticate_user(db, request.username, request.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    user.last_login = datetime.utcnow()
    db.commit()
    db.refresh(user)
    token = AuthService.create_access_token({"sub": user.username})
    return {"access_token": token, "token_type": "bearer", "user": user}


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_active_user)):
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

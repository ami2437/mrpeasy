"""Securely bootstrap the first super-admin account."""
import getpass
import os
import sys
from pathlib import Path

# Add the parent directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from app.config.database import SessionLocal
from app.services.auth import AuthService


def _required_value(environment_name: str, prompt: str, secret: bool = False) -> str:
    value = os.getenv(environment_name)
    if value:
        return value.strip()
    reader = getpass.getpass if secret else input
    return reader(prompt).strip()


def create_super_admin():
    """Create a super-admin account without embedding or displaying credentials."""
    username = _required_value("SUPER_ADMIN_USERNAME", "Username: ")
    email = _required_value("SUPER_ADMIN_EMAIL", "Email: ")
    full_name = _required_value("SUPER_ADMIN_FULL_NAME", "Full name: ")
    password = _required_value("SUPER_ADMIN_PASSWORD", "Temporary password: ", secret=True)
    if not username or not email or not full_name:
        raise ValueError("Username, email, and full name are required")
    if len(password) < 10:
        raise ValueError("Temporary password must contain at least 10 characters")

    db = SessionLocal()
    try:
        existing_user = AuthService.get_user_by_username(db, username)
        if existing_user:
            print(f"User '{username}' already exists with role '{existing_user.role}'.")
            return

        user = AuthService.create_user(
            db=db,
            username=username,
            email=email,
            password=password,
            full_name=full_name,
            role="super_admin"
        )
        db.commit()
        db.refresh(user)

        print(f"Super-admin account '{user.username}' created successfully.")
    except Exception as e:
        db.rollback()
        print(f"Error creating super-admin account: {str(e)}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    create_super_admin()

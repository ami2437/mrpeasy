"""Initialize database tables and securely bootstrap a super-admin account."""
import sys
from pathlib import Path

# Add the parent directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from app.models import Base
from app.config.database import engine
from create_owner_user import create_super_admin


def init_db_and_create_super_admin():
    """Initialize database tables and create the first super-admin account."""
    try:
        print("Creating database tables...")
        Base.metadata.create_all(bind=engine)
        print("Database tables created successfully.")
        create_super_admin()
    except Exception as e:
        print(f"Initialization failed: {str(e)}")
        raise


if __name__ == "__main__":
    init_db_and_create_super_admin()

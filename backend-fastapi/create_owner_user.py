"""
Script to create owner user: amittal
"""
import sys
from pathlib import Path

# Add the parent directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from app.config.database import SessionLocal
from app.services.auth import AuthService

def create_owner_user():
    """Create owner user amittal"""
    db = SessionLocal()
    try:
        # Check if user already exists
        existing_user = AuthService.get_user_by_username(db, "amittal")
        if existing_user:
            print("❌ User 'amittal' already exists!")
            print(f"   Username: {existing_user.username}")
            print(f"   Email: {existing_user.email}")
            print(f"   Role: {existing_user.role}")
            return
        
        # Create owner user
        print("🔧 Creating owner user...")
        user = AuthService.create_user(
            db=db,
            username="amittal",
            email="amittal@americantraders.com",
            password="test1",
            full_name="Amit Mittal",
            role="owner"
        )
        
        db.commit()
        db.refresh(user)
        
        print("✅ Owner user created successfully!")
        print(f"   Username: {user.username}")
        print(f"   Email: {user.email}")
        print(f"   Full Name: {user.full_name}")
        print(f"   Role: {user.role}")
        print(f"   Active: {user.is_active}")
        print("\n🔐 Login Credentials:")
        print(f"   Username: amittal")
        print(f"   Password: test1")
        
    except Exception as e:
        print(f"❌ Error creating user: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    create_owner_user()

"""
Initialize database and create owner user
"""
import sys
from pathlib import Path

# Add the parent directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from app.models import Base, User, Role, CustomerOrder, StockItem, ManufacturingOrder, Vendor, Inventory, SyncLog, ShipmentBox, Label
from app.config.database import engine, SessionLocal
from app.services.auth import AuthService

def init_db_and_create_owner():
    """Initialize database tables and create owner user"""
    try:
        # Create all tables
        print("🔧 Creating database tables...")
        Base.metadata.create_all(bind=engine)
        print("✅ Database tables created successfully!")
        
        # Create owner user
        db = SessionLocal()
        try:
            # Check if user already exists
            existing_user = AuthService.get_user_by_username(db, "amittal")
            if existing_user:
                print("\n❌ User 'amittal' already exists!")
                print(f"   Username: {existing_user.username}")
                print(f"   Email: {existing_user.email}")
                print(f"   Role: {existing_user.role}")
                return
            
            # Create owner user
            print("\n🔧 Creating owner user...")
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
            print("\n📊 You can now:")
            print("   1. Start the FastAPI server: uvicorn app.main:app --reload")
            print("   2. Login at: POST http://localhost:8000/api/auth/login")
            print("   3. Access Swagger UI at: http://localhost:8000/docs")
            
        finally:
            db.close()
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    init_db_and_create_owner()

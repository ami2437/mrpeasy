"""
Get all pending customer orders from MRPeasy
"""
import sys
from pathlib import Path

# Add the parent directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from app.config.database import SessionLocal
from app.services.sync_service import SyncService
from app.models import CustomerOrder
import json

def get_pending_orders():
    """Sync and get all pending customer orders"""
    db = SessionLocal()
    try:
        # First sync customer orders from MRPeasy
        print("🔄 Syncing customer orders from MRPeasy...")
        sync_result = SyncService.sync_customer_orders(db)
        
        if sync_result.get("success"):
            print(f"✅ Synced {sync_result.get('synced_count', 0)} customer orders")
        else:
            print(f"❌ Sync error: {sync_result.get('error')}")
            return
        
        # Query for pending orders (status varies, let's get all and filter)
        print("\n📋 Fetching pending customer orders...")
        all_orders = db.query(CustomerOrder).all()
        
        # Display all orders with their status
        print(f"\n📊 Total orders in database: {len(all_orders)}")
        print("\n" + "="*100)
        
        pending_statuses = ['pending', 'new', 'open', 'confirmed']
        
        for order in all_orders:
            # Check if status text indicates pending/open
            status_lower = order.status_txt.lower() if order.status_txt else ''
            is_pending = any(status in status_lower for status in pending_statuses) or order.status < 20
            
            if is_pending:
                print(f"\n🔵 PENDING ORDER")
            else:
                print(f"\n✅ {order.status_txt}")
                
            print(f"   Order Code: {order.code}")
            print(f"   Customer: {order.customer_name}")
            print(f"   Reference: {order.reference or 'N/A'}")
            print(f"   Status: {order.status_txt} (ID: {order.status})")
            print(f"   Total: {order.currency or ''} {order.total_price or 'N/A'}")
            print(f"   Delivery Date: {order.delivery_date or 'Not set'}")
            print(f"   Notes: {order.notes[:100] if order.notes else 'None'}...")
            print("-" * 100)
        
        # Count pending orders
        pending_count = sum(1 for order in all_orders 
                          if any(status in (order.status_txt.lower() if order.status_txt else '') 
                                for status in pending_statuses) or order.status < 20)
        
        print(f"\n📊 Summary:")
        print(f"   Total Orders: {len(all_orders)}")
        print(f"   Pending Orders: {pending_count}")
        print(f"   Completed Orders: {len(all_orders) - pending_count}")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    get_pending_orders()

"""
Test script to fetch shipments from MRPeasy API
"""
import sys
from pathlib import Path

# Add the parent directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.mrpeasy_client import mrpeasy_client
import json

def test_get_shipments():
    """Test getting shipments from MRPeasy API"""
    try:
        print("🔍 Fetching shipments from MRPeasy API...")
        print(f"API URL: {mrpeasy_client.base_url}/shipments")
        print(f"API Key: {mrpeasy_client.auth.username[:10]}...")
        print("-" * 60)
        
        shipments = mrpeasy_client.get_shipments()
        
        if shipments:
            print(f"✅ Success! Retrieved {len(shipments)} shipment(s)")
            print("\n📦 Shipments Data:")
            print(json.dumps(shipments, indent=2))
        else:
            print("⚠️  No shipments found or empty response")
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_get_shipments()

# Data Flow Architecture

## Important: MRPeasy READ-ONLY Mode

This application **ONLY READS DATA** from MRPeasy. It NEVER sends, modifies, or deletes data in MRPeasy.

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                      MRPeasy System                         │
│            (Production Data - READ ONLY)                    │
└────────────────────────┬────────────────────────────────────┘
                         │
                         │ GET requests only
                         │ (fetch data)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   FastAPI Backend                           │
│                                                             │
│  • MRPeasyAPIClient - ONLY GET methods                     │
│  • SyncService - ONLY reads & stores locally              │
│  • CRUD Service - Only modifies LOCAL database            │
└─────────────────────────────────────────────────────────────┘
                         │
                         │ local database operations
                         │ (create/read/update/delete)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              Local Database (SQLite/PostgreSQL)            │
│                                                             │
│  • CustomerOrder     (synced read-only copy)              │
│  • StockItem         (synced read-only copy)              │
│  • ManufacturingOrder (synced read-only copy)             │
│  • Vendor            (synced read-only copy)              │
│  • Inventory         (historical snapshots)               │
│  • SyncLog           (tracks data refreshes)              │
└─────────────────────────────────────────────────────────────┘
                         │
                         │ JSON responses
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                  React Frontend                            │
│                                                             │
│  • Display data from local API                            │
│  • Allow local portal customizations                      │
│  • Cannot modify MRPeasy data                             │
└─────────────────────────────────────────────────────────────┘
```

## API Operations

### ✅ ALLOWED - Reading from MRPeasy

```
GET /customer-orders          → Fetch from local DB (synced from MRPeasy)
GET /stock-items              → Fetch from local DB (synced from MRPeasy)
GET /manufacturing-orders     → Fetch from local DB (synced from MRPeasy)
GET /vendors                  → Fetch from local DB (synced from MRPeasy)
POST /sync/all                → Refresh local copies FROM MRPeasy (no write)
```

### ✅ ALLOWED - Local Database Operations

```
PUT /customer-orders/{id}     → Update LOCAL database ONLY
DELETE /customer-orders/{id}  → Delete LOCAL database ONLY
(Similar for other entities)
```

### ❌ BLOCKED - Never Sent to MRPeasy

```
POST /customer-orders    ← Not sent to MRPeasy
PUT /customer-orders     ← Not sent to MRPeasy
DELETE /customer-orders  ← Not sent to MRPeasy
(All write operations stay LOCAL)
```

## MRPeasyAPIClient Methods

All methods are READ-ONLY:

```python
# ✅ GET only methods
get_customer_orders()           # Read from API
get_customer_order(id)          # Read from API
get_stock_items()               # Read from API
get_stock_item(id)              # Read from API
get_manufacturing_orders()      # Read from API
get_manufacturing_order(id)     # Read from API
get_vendors()                   # Read from API
get_inventory()                 # Read from API
get_report(type)                # Read from API

# ❌ REMOVED - never implemented
# create_customer_order()        # REMOVED
# update_customer_order()        # REMOVED
# (No write methods exist)
```

## Data Sync Strategy

### How Syncing Works

1. **Manual Sync** - Call `/sync/all` endpoint to refresh data
2. **One-Way Sync** - Data flows MRPeasy → Local DB only
3. **No Pushing Back** - Local changes are NEVER sent to MRPeasy
4. **Sync Log** - Track all sync operations

### Sync Process

```python
# Example: Syncing customer orders

1. Fetch all orders from MRPeasy API (GET request)
2. For each order:
   - Check if it exists in local DB
   - If exists: UPDATE local record
   - If new: INSERT into local DB
3. Record sync completion in SyncLog table
4. Return summary to frontend

# NO data is sent back to MRPeasy
```

## Local Portal Customization

### What You CAN Do Locally

- ✅ Add custom fields to local database
- ✅ Create custom views/filters
- ✅ Track custom statuses for items
- ✅ Add notes and comments locally
- ✅ Export/transform data for reporting
- ✅ Create snapshots/history

### These Changes Stay LOCAL

All customizations are stored in your database, not in MRPeasy.

## Example: Adding Custom Fields

```python
# In app/models/__init__.py - ADD custom field
class CustomerOrder(Base):
    __tablename__ = "customer_orders"
    
    # MRPeasy synced fields
    mrp_cust_ord_id = Column(Integer, unique=True)
    code = Column(String)
    customer_name = Column(String)
    status = Column(Integer)
    
    # YOUR LOCAL CUSTOM FIELDS (never synced back to MRPeasy)
    internal_notes = Column(Text, nullable=True)
    custom_status = Column(String, nullable=True)
    approval_date = Column(DateTime, nullable=True)
    custom_priority = Column(Integer, nullable=True)
```

These fields are ONLY in your local database.

## Security & Data Protection

1. **Read-Only on MRPeasy** - Your production data is protected
2. **Local Modifications** - Changes don't affect MRPeasy
3. **No Reverse Sync** - We never push data back
4. **Audit Trail** - SyncLog tracks all MRPeasy fetches
5. **Data Isolation** - Each sync overwrites with fresh MRPeasy data

## Troubleshooting

### "I made changes but they're gone after sync"

This is expected! After syncing, local changes are overwritten with fresh MRPeasy data. This is by design to keep data consistent.

**Solution:** Store custom fields in separate tables or sync less frequently.

### "I want to modify MRPeasy data"

Direct modifications to MRPeasy are not supported through this API. Use MRPeasy's native interface or API directly for that.

### "Can I schedule automatic syncs?"

Yes! Create a background task:

```python
# app/tasks/scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()

@scheduler.scheduled_job('interval', hours=1)
def sync_data():
    """Sync MRPeasy data every hour"""
    db = SessionLocal()
    SyncService.sync_all(db)
    db.close()

scheduler.start()
```

## Summary

| Operation | Location | Direction |
|-----------|----------|-----------|
| Read MRPeasy Data | `/sync/*` | MRPeasy → Local DB |
| Display Data | `/customer-orders` etc | Local DB → Frontend |
| Modify Locally | `PUT /customer-orders/{id}` | Frontend → Local DB |
| Backup/History | Local DB | One-way storage |
| Send to MRPeasy | **DISABLED** | ❌ Never |

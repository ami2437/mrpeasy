# MRPeasy FastAPI Backend

High-performance FastAPI backend for MRPeasy custom portal with SQLite database and SQLAlchemy ORM.

## ⚠️ Important: READ-ONLY Mode

**This application ONLY READS data from MRPeasy. It NEVER sends, modifies, or deletes data in MRPeasy.**

- ✅ Fetches/syncs data from MRPeasy (GET requests only)
- ✅ Stores data locally in your database
- ✅ Allows customization and modifications locally
- ❌ Never modifies MRPeasy production data
- ❌ Never sends write requests to MRPeasy

See [DATA_FLOW.md](DATA_FLOW.md) for architecture details.

## Features

- **FastAPI**: Modern, fast web framework for building APIs
- **SQLAlchemy ORM**: Easy database model management and customization
- **SQLite Database**: Lightweight, file-based database (easily switchable to PostgreSQL)
- **Data Sync**: Automatic sync from MRPeasy API to local database
- **RESTful API**: Clean API endpoints for all operations
- **Automatic Docs**: Interactive API documentation via Swagger UI

## Database Options

### SQLite (Default - Recommended for Development)
- File-based, zero configuration
- Perfect for development and prototyping
- Located at: `mrpeasy.db`

### PostgreSQL (Production)
- Scalable and robust
- Better for multi-user scenarios
- Update `DATABASE_URL` in `.env`

## Setup

### Prerequisites
- Python 3.9+
- pip

### Installation

1. **Create virtual environment**:
   ```bash
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # macOS/Linux:
   source venv/bin/activate
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**:
   ```bash
   cp .env.example .env
   ```
   
   Edit `.env` with your MRPeasy API credentials:
   ```
   MRPEASY_API_KEY=your_api_key
   MRPEASY_API_SECRET=your_api_secret
   ```

4. **Run server**:
   ```bash
   python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

Server runs on `http://localhost:8000`

### Access API Documentation
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## API Endpoints

### Customer Orders
- `GET /customer-orders` - List all orders
- `GET /customer-orders/{id}` - Get order details
- `POST /customer-orders` - Create order
- `PUT /customer-orders/{id}` - Update order
- `DELETE /customer-orders/{id}` - Delete order

### Stock Items
- `GET /stock-items` - List all items
- `GET /stock-items/{id}` - Get item details
- `GET /stock-items/search?q=query` - Search items
- `GET /stock-items/low-stock` - Get low stock items

### Manufacturing Orders
- `GET /manufacturing-orders` - List all orders
- `GET /manufacturing-orders/{id}` - Get order details
- `GET /manufacturing-orders/active` - Get active orders

### Vendors
- `GET /vendors` - List all vendors

### Sync Operations
- `POST /sync/customer-orders` - Sync customer orders
- `POST /sync/stock-items` - Sync stock items
- `POST /sync/manufacturing-orders` - Sync manufacturing orders
- `POST /sync/all` - Sync all data

## Database Models

All models are easily customizable. Edit files in `app/models/` to add/modify fields.

### CustomerOrder
- id, code, customer_id, status, total_price, delivery_date, notes, etc.

### StockItem
- id, code, title, quantity, price, cost, unit, group, etc.

### ManufacturingOrder
- id, code, quantity, status, due_date, total_cost, etc.

### Vendor
- id, code, title, currency, tax_rate, payment_period, etc.

### Inventory
- Snapshots of inventory at specific dates

### SyncLog
- Tracks all syncs from MRPeasy API

## Adding Custom Fields

1. **Edit model** in `app/models/__init__.py`:
   ```python
   class StockItem(Base):
       __tablename__ = "stock_items"
       
       # Add new column:
       custom_field = Column(String, nullable=True)
   ```

2. **Update schema** in `app/schemas/__init__.py`:
   ```python
   class StockItemResponse(BaseModel):
       custom_field: Optional[str] = None
   ```

3. **Database auto-creates**: SQLAlchemy handles the migration

## Project Structure

```
backend-fastapi/
├── app/
│   ├── config/
│   │   ├── settings.py      # Configuration
│   │   └── database.py      # Database setup
│   ├── models/
│   │   └── __init__.py      # SQLAlchemy models
│   ├── schemas/
│   │   └── __init__.py      # Pydantic schemas
│   ├── services/
│   │   ├── mrpeasy_client.py    # MRPeasy API client
│   │   ├── sync_service.py      # Sync logic
│   │   └── crud.py              # Database operations
│   ├── routes/
│   │   ├── customer_orders.py
│   │   ├── stock_items.py
│   │   ├── manufacturing_orders.py
│   │   ├── vendors.py
│   │   └── sync.py
│   └── main.py              # FastAPI app
├── requirements.txt
├── .env.example
└── README.md
```

## Development Tips

1. **Auto-reload**: Server reloads on code changes
2. **Interactive Docs**: Use `/docs` to test endpoints
3. **Database Inspection**: Open `mrpeasy.db` with SQLite viewer
4. **Add Models**: Create in `app/models/`, routes automatically work

## Switching to PostgreSQL

1. **Install driver**:
   ```bash
   pip install psycopg2-binary
   ```

2. **Update `.env`**:
   ```
   DATABASE_URL=postgresql://username:password@localhost:5432/mrpeasy
   ```

3. **Restart server**: Changes apply automatically

## Next Steps

- Add authentication
- Create scheduled sync tasks
- Add more custom fields
- Deploy to production

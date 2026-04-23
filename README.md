# MRPeasy Custom Manufacturing Portal

A full-stack application that integrates with the MRPeasy API to create a customized manufacturing portal with data manipulation and visualization capabilities.

## Project Structure

```
mrpeasy/
├── backend-fastapi/  # FastAPI Python backend
│   ├── app/
│   │   ├── config/          # Settings & database config
│   │   ├── models/          # SQLAlchemy ORM models
│   │   ├── schemas/         # Pydantic schemas
│   │   ├── services/        # Business logic & sync
│   │   ├── routes/          # API endpoints
│   │   └── main.py          # FastAPI app
│   ├── requirements.txt
│   ├── .env.example
│   └── README.md
│
├── frontend/         # React web portal
│   ├── public/       # Static assets
│   ├── src/
│   │   ├── components/   # React components
│   │   ├── pages/        # Page components
│   │   ├── services/     # API client
│   │   └── App.js
│   ├── package.json
│   └── README.md
│
└── README.md         # This file
```

## Quick Start

### Prerequisites
- Python 3.9+ (for backend)
- Node.js 14+ (for frontend)
- npm or yarn

### 1. Setup Backend

```bash
cd backend-fastapi

# Windows
setup.bat

# macOS/Linux
bash setup.sh
```

Then configure credentials:
```bash
cp .env.example .env
# Edit .env with your MRPeasy API credentials
```

Start the FastAPI server:
```bash
# Activate virtual environment first
venv\Scripts\activate  # Windows
source venv/bin/activate  # macOS/Linux

python -m uvicorn app.main:app --reload
```

Backend runs on `http://localhost:8000`
- API Docs: `http://localhost:8000/docs`
- Alternative Docs: `http://localhost:8000/redoc`

### 2. Setup Frontend

In a new terminal:

```bash
cd frontend
npm install
npm start
```

Frontend runs on `http://localhost:3000`

## Features

### Backend (FastAPI + SQLAlchemy)
- **Modern Framework**: FastAPI with async support
- **Flexible Database**: SQLite (default) or PostgreSQL
- **ORM Layer**: SQLAlchemy for easy field customization
- **Auto Sync**: Sync data from MRPeasy API automatically
- **Interactive Docs**: Swagger UI & ReDoc for testing
- **Customizable Models**: Add fields easily without migrations

### Frontend Features

- **Dashboard**: Overview of key metrics
- **Orders Management**: View and manage customer orders
- **Inventory Tracking**: Real-time stock level monitoring
- **Manufacturing Monitoring**: Track production orders
- **Vendor Management**: Manage supplier information
- **Responsive UI**: Works on all devices

## API Integration Points

The backend integrates with MRPeasy's REST API to:

1. **Fetch Data**: Retrieve customer orders, stock items, manufacturing orders, vendors
2. **Create Records**: Add new customer orders, items, etc.
3. **Update Records**: Modify existing orders and items
4. **Generate Reports**: Access production and CRM reports
5. **Track Inventory**: Monitor stock levels and movements

## Database Customization

### Adding Custom Fields (Easy!)

1. **Open database model** - `backend-fastapi/app/models/__init__.py`
2. **Add new column** to any model:
   ```python
   class StockItem(Base):
       __tablename__ = "stock_items"
       
       # Existing fields...
       # Add your custom field:
       custom_status = Column(String, nullable=True)
       warehouse_location = Column(String, nullable=True)
       supplier_notes = Column(Text, nullable=True)
   ```

3. **Update schema** - `backend-fastapi/app/schemas/__init__.py`
4. **Restart server** - Database updates automatically!

### Switching Databases

**From SQLite to PostgreSQL:**

```bash
# Install PostgreSQL driver
pip install psycopg2-binary
```

Edit `backend-fastapi/.env`:
```
DATABASE_URL=postgresql://user:password@localhost:5432/mrpeasy
```

Restart server - that's it!

## Environment Variables

### Backend (.env)
- `MRPEASY_API_BASE_URL` - MRPeasy API base URL
- `MRPEASY_API_KEY` - Your API key
- `MRPEASY_API_SECRET` - Your API secret
- `DATABASE_URL` - Database connection string
- `PORT` - Server port (default: 8000)
- `DEBUG` - Debug mode (True/False)
- `CORS_ORIGINS` - List of allowed origins

## Troubleshooting

### Backend won't start
- Ensure Python 3.9+ is installed: `python --version`
- Check virtual environment is activated
- Verify dependencies: `pip list`
- Check if port 8000 is available

### API calls failing
- Verify MRPeasy API credentials in .env
- Check MRPeasy API rate limits (100 requests/10 seconds)
- Try accessing `http://localhost:8000/health`

### Database issues
- For SQLite: Delete `mrpeasy.db` to reset
- For PostgreSQL: Check connection string in .env
- Models update automatically on restart

### Frontend won't connect
- Ensure backend is running on port 8000
- Check CORS settings in backend .env
- Try accessing `http://localhost:8000/docs`

## Next Steps

1. **Authentication**: Add user login/authentication
2. **Database**: Add local database for caching frequently used data
3. **Advanced Features**: 
   - Custom reports
   - Data export (CSV/Excel)
   - Real-time notifications
   - User dashboard customization
4. **Testing**: Add unit and integration tests
5. **Deployment**: Deploy to production server

## Support

For MRPeasy API documentation, visit: https://www.mrpeasy.com/resources/api/

## License

ISC

---

**Created**: February 2026
**Version**: 1.0.0

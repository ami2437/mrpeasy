from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.config.settings import settings
from app.config.database import engine, Base
from app.models import CustomerOrder, StockItem, ManufacturingOrder, Vendor, Inventory, SyncLog, User, Role
from app.routes import customer_orders, stock_items, manufacturing_orders, vendors, sync, auth, labels, invoicing, reports

# Create tables
Base.metadata.create_all(bind=engine)

# Create FastAPI app
app = FastAPI(
    title="MRPeasy Custom Portal API",
    description="FastAPI backend for MRPeasy custom manufacturing portal (READ-ONLY mode - never modifies MRPeasy)",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check endpoint
@app.get("/api/health")
def health_check():
    return {
        "status": "OK",
        "message": "MRPeasy Custom Portal Backend is running (READ-ONLY mode)",
        "mode": "read-only",
        "mrpeasy_protection": "ENABLED - No write requests to MRPeasy"
    }


# Include routes FIRST (so they take priority)
app.include_router(auth.router)
app.include_router(labels.router)
app.include_router(invoicing.router)
app.include_router(customer_orders.router)
app.include_router(reports.router)
app.include_router(stock_items.router)
app.include_router(manufacturing_orders.router)
app.include_router(vendors.router)
app.include_router(sync.router)


@app.get("/api")
def root():
    return {
        "message": "MRPeasy Custom Portal API",
        "mode": "READ-ONLY",
        "docs": "/docs",
        "architecture": "/docs#/",
        "data_flow": "See DATA_FLOW.md in repository",
        "version": "1.0.0",
        "safety": "This API never sends, modifies, or deletes data in MRPeasy"
    }


# Mount static files LAST (so they don't interfere with API routes)
static_dir = Path(__file__).parent.parent.parent / "frontend" / "public"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )

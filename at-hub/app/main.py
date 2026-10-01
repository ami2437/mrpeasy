from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config.settings import settings
from app.config.database import engine, SessionLocal, add_missing_columns
from app.models import Base
from app.services.auth import seed_admin_user, ensure_super_admin
from app.services.crud import ShipmentService, ProductGroupService, backfill_lot_costing, backfill_line_identity
from app.services.test_data import ensure_test_data
from app.routes import (
    auth, stock_items, customers, vendors, customer_orders, purchase_orders, lots, shipments, invoicing, company,
    landed_costs, test_data, users, attachments,
)

Base.metadata.create_all(bind=engine)
add_missing_columns(Base)

db = SessionLocal()
try:
    seed_admin_user(db)
    ensure_super_admin(db)
    ProductGroupService.ensure_defaults(db)
    ShipmentService.reconcile_bookings(db)
    backfill_lot_costing(db)
    backfill_line_identity(db)
    if settings.test_data_enabled:
        ensure_test_data(db)
finally:
    db.close()

app = FastAPI(title="AT-HUB", description="Standalone warehouse, orders, and purchasing app", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    """Make browsers revalidate pages/JS/CSS on every load, so a new auth-guard.js
    is never mixed with an old cached page (or vice versa) after an update."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/health")
def health_check():
    return {"status": "OK", "app": "AT-HUB"}


app.include_router(auth.router)
app.include_router(stock_items.router)
app.include_router(customers.router)
app.include_router(vendors.router)
app.include_router(customer_orders.router)
app.include_router(purchase_orders.router)
app.include_router(lots.router)
app.include_router(shipments.router)
app.include_router(invoicing.router)
app.include_router(company.router)
app.include_router(landed_costs.router)
app.include_router(test_data.router)
app.include_router(users.router)
app.include_router(attachments.router)

frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="static")

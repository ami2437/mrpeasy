import json
import re
from pathlib import Path
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config.settings import settings
from app.config.database import engine, SessionLocal, add_missing_columns
from app.models import NumberSeries
from app.models import Base
from app.services.auth import AuthService, seed_admin_user, ensure_super_admin
from app.services.crud import ShipmentService, ProductGroupService, backfill_lot_costing, backfill_line_identity, backfill_vendor_codes
from app.services.test_data import ensure_test_data
from app.routes import (
    auth, stock_items, customers, vendors, customer_orders, purchase_orders, lots, shipments, invoicing, company,
    landed_costs, test_data, users, attachments, invoice_funding, ai_orders, ai_docs, mtrs, vendor_payments, reports,
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
    backfill_vendor_codes(db)
    # Never seed TEST records into a database built by the MRPeasy import (it carries number series).
    if settings.test_data_enabled and not db.query(NumberSeries).first():
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


# Employees never see dollar amounts: any money field in an API response is blanked
# for them, whatever page or endpoint asked for it.
MONEY_KEY = re.compile(r"(price|cost|amount|total|balance|paid|revenue|profit|margin|charge|funding|discount)", re.I)


def _scrub(value):
    if isinstance(value, dict):
        return {k: (None if MONEY_KEY.search(k) and not isinstance(v, (dict, list)) else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


@app.middleware("http")
async def hide_money_from_employees(request, call_next):
    response = await call_next(request)
    auth = request.headers.get("authorization", "")
    if (not request.url.path.startswith("/api/") or not auth.lower().startswith("bearer ")
            or "application/json" not in response.headers.get("content-type", "")):
        return response
    payload = AuthService.decode_token(auth[7:]) or {}
    db = SessionLocal()
    try:
        user = AuthService.get_user_by_username(db, payload.get("sub")) if payload.get("sub") else None
        role = user.role if user else None
    finally:
        db.close()
    body = b"".join([chunk async for chunk in response.body_iterator])
    if role == "employee":
        try:
            body = json.dumps(_scrub(json.loads(body))).encode()
        except ValueError:
            pass
    headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
    return Response(content=body, status_code=response.status_code, headers=headers, media_type=response.media_type)


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
app.include_router(invoice_funding.router)
app.include_router(ai_orders.router)
app.include_router(ai_docs.router)
app.include_router(mtrs.router)
app.include_router(vendor_payments.router)
app.include_router(reports.router)

frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="static")

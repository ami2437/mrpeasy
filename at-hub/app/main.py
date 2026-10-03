import json
import re
from pathlib import Path
from fastapi import Header, HTTPException, FastAPI, Response
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


# ---- recycle bin: every delete is kept and can be restored ----
from app.services import recycle_bin  # noqa: E402
recycle_bin.install()


@app.middleware("http")
async def who_is_asking(request, call_next):
    """The signed-in user, for the recycle bin's 'deleted by'."""
    auth = request.headers.get("authorization", "")
    token = recycle_bin.current_user.set((AuthService.decode_token(auth[7:]) or {}).get("sub") if auth.lower().startswith("bearer ") else None)
    try:
        return await call_next(request)
    finally:
        recycle_bin.current_user.reset(token)


def _bin_user(authorization: str):
    from app.dependencies import ROLE_RANK
    payload = AuthService.decode_token((authorization or "").split(" ")[-1]) if authorization else None
    db = SessionLocal()
    try:
        user = AuthService.get_user_by_username(db, payload.get("sub")) if payload else None
    finally:
        db.close()
    if not user or ROLE_RANK.get(user.role, 0) < ROLE_RANK["manager"]:
        raise HTTPException(status_code=403, detail="The recycle bin needs the manager role")
    recycle_bin.current_user.set(user.username)
    return user


@app.get("/api/recycle-bin")
def recycle_bin_list(authorization: str = Header(None)):
    import json as _json
    from app.models import DeletedRecord
    _bin_user(authorization)
    db = SessionLocal()
    try:
        out = []
        for e in db.query(DeletedRecord).order_by(DeletedRecord.deleted_at.desc()).limit(500).all():
            rows = _json.loads(e.rows)
            counts = {}
            for r in rows:
                counts[r["table"]] = counts.get(r["table"], 0) + 1
            out.append({"id": e.id, "kind": e.kind, "label": e.label, "deleted_by": e.deleted_by,
                        "deleted_at": e.deleted_at.isoformat() + "Z", "restored_at": e.restored_at.isoformat() + "Z" if e.restored_at else None,
                        "restored_by": e.restored_by, "contents": counts})
        return out
    finally:
        db.close()


@app.post("/api/recycle-bin/{entry_id}/restore")
def recycle_bin_restore(entry_id: int, authorization: str = Header(None)):
    _bin_user(authorization)
    db = SessionLocal()
    try:
        return recycle_bin.restore(db, entry_id)
    finally:
        db.close()


@app.delete("/api/recycle-bin/{entry_id}", status_code=204)
def recycle_bin_purge(entry_id: int, authorization: str = Header(None)):
    _bin_user(authorization)
    db = SessionLocal()
    try:
        recycle_bin.purge(db, entry_id)
    finally:
        db.close()
    return Response(status_code=204)


# ---- activity history: every successful change to an order or PO, with who and what ----
ACTIVITY_PATH = re.compile(r"^/api/(customer-orders|purchase-orders)/(\d+)(?:/(.*))?$")


@app.middleware("http")
async def record_activity(request, call_next):
    m = ACTIVITY_PATH.match(request.url.path) if request.method in ("POST", "PUT", "DELETE") else None
    body = b""
    if m and "application/json" in request.headers.get("content-type", ""):
        body = await request.body()
    response = await call_next(request)
    if m and response.status_code < 400:
        from app.models import ActivityLog
        auth = request.headers.get("authorization", "")
        who = (AuthService.decode_token(auth[7:]) or {}).get("sub") if auth.lower().startswith("bearer ") else None
        kind, rec_id, rest = m.group(1), int(m.group(2)), (m.group(3) or "")
        if rest.startswith("profit") or rest.endswith(".pdf") or rest.startswith("email"):
            return response
        db = SessionLocal()
        try:
            db.add(ActivityLog(entity_type="customer_order" if kind == "customer-orders" else "purchase_order", entity_id=rec_id,
                               method=request.method, action=rest, detail=body.decode("utf-8", "replace")[:2000] or None, by=who))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
    return response


@app.get("/api/activity/{entity_type}/{entity_id}")
def activity(entity_type: str, entity_id: int, authorization: str = Header(None)):
    """The change history of one order / PO, newest first."""
    from app.models import ActivityLog
    if not authorization or not AuthService.decode_token(authorization.split(" ")[-1]):
        raise HTTPException(status_code=401, detail="Not signed in")
    db = SessionLocal()
    try:
        payload = AuthService.decode_token(authorization.split(" ")[-1]) or {}
        user = AuthService.get_user_by_username(db, payload.get("sub"))
        employee = not user or user.role == "employee"
        rows = (db.query(ActivityLog).filter(ActivityLog.entity_type == entity_type, ActivityLog.entity_id == entity_id)
                .order_by(ActivityLog.at.desc()).limit(300).all())

        def detail(text):
            if not employee or not text:
                return text
            try:  # employees never see prices or costs, not even inside the history text
                return json.dumps(_scrub(json.loads(text)))
            except ValueError:
                return None
        return [{"method": r.method, "action": r.action, "detail": detail(r.detail), "by": r.by, "at": r.at.isoformat() + "Z"} for r in rows]
    finally:
        db.close()


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
from app.routes import imports  # noqa: E402
app.include_router(imports.router)

frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="static")

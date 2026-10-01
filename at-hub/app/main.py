from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config.settings import settings
from app.config.database import engine, SessionLocal
from app.models import Base
from app.services.auth import seed_admin_user
from app.routes import auth, stock_items, customers, vendors, customer_orders, purchase_orders, lots

Base.metadata.create_all(bind=engine)

db = SessionLocal()
try:
    seed_admin_user(db)
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

frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="static")

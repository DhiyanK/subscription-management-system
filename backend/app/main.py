from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from backend.app.core import settings
from backend.app.routers import auth, billing, catalog, customers, dashboard, discounts, plans, quotations, subscriptions

app = FastAPI(title="Brightloop Billing — Subscription Management", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])
app.include_router(auth.router)
app.include_router(plans.router)
app.include_router(dashboard.router)
app.include_router(subscriptions.router)
app.include_router(billing.router)
app.include_router(catalog.router)
app.include_router(customers.router)
app.include_router(discounts.router)
app.include_router(quotations.router)
app.include_router(quotations.templates_router)

@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "service": "subscription-management"}
from pathlib import Path
from fastapi.staticfiles import StaticFiles

_frontend = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/static", StaticFiles(directory=_frontend), name="static")

@app.get("/", include_in_schema=False)
def dashboard_page():
    return FileResponse(_frontend / "index.html")

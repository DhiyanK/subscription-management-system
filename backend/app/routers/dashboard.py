from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from backend.app.database import get_db
from backend.app.dependencies import require_roles
from backend.app.models import Invoice, Payment, Plan, Subscription, User

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
@router.get("/summary")
def summary(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "staff"))):
    active = db.scalar(select(func.count()).select_from(Subscription).where(Subscription.status == "active")) or 0
    customers = db.scalar(select(func.count()).select_from(User).where(User.role == "customer", User.is_active.is_(True))) or 0
    invoices = db.scalar(select(func.count()).select_from(Invoice)) or 0
    outstanding = db.scalar(select(func.coalesce(func.sum(Invoice.amount_due), 0)).where(Invoice.status.in_(["open", "confirmed"]))) or 0
    received = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "succeeded")) or 0
    overdue = db.scalar(select(func.count()).select_from(Invoice).where(Invoice.status.in_(["open", "confirmed"]), Invoice.due_at < datetime.now(timezone.utc))) or 0
    mrr = db.scalar(select(func.coalesce(func.sum(Plan.price * Subscription.quantity), 0)).join(Subscription, Subscription.plan_id == Plan.id).where(Subscription.status == "active", Plan.interval == "monthly")) or 0
    by_status = dict(db.execute(select(Invoice.status, func.count()).group_by(Invoice.status)).all())
    return {"active_subscriptions": active, "active_customers": customers, "invoice_count": invoices, "outstanding_amount": str(outstanding), "revenue_received": str(received), "overdue_invoices": overdue, "monthly_recurring_revenue": str(mrr), "invoice_status": by_status, "currency": "INR"}

@router.get("/invoices")
def invoice_report(status: str | None = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "staff"))):
    limit = max(1, min(limit, 100))
    query = select(Invoice).order_by(Invoice.due_at.desc()).offset(offset).limit(limit)
    if status:
        query = query.where(Invoice.status == status)
    return [{"id": i.id, "number": i.number, "subscription_id": i.subscription_id, "amount_due": str(i.amount_due), "currency": i.currency, "status": i.status, "due_at": i.due_at, "overdue": i.status in {"open", "confirmed"} and i.due_at < datetime.now(timezone.utc)} for i in db.scalars(query)]

@router.get("/revenue-trend")
def revenue_trend(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "staff"))):
    from calendar import month_name
    from datetime import date
    today = datetime.now(timezone.utc).date()
    months = []
    for offset in range(11, -1, -1):
        month_index = today.month - offset
        year = today.year + (month_index - 1) // 12
        month = (month_index - 1) % 12 + 1
        months.append(date(year, month, 1))
    values = []
    for start in months:
        end = date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
        total = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "succeeded", Payment.created_at >= start, Payment.created_at < end)) or 0
        values.append({"month": month_name[start.month][:3], "revenue": float(total)})
    return values

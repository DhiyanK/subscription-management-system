from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.database import get_db
from backend.app.dependencies import current_user, require_roles
from backend.app.models import Plan, Quotation, QuotationTemplate, Subscription, User
from backend.app.services.billing_dates import add_interval
from backend.app.services.recurring_billing import issue_invoice_for_subscription

router = APIRouter(prefix="/api/quotations", tags=["quotations"])
templates_router = APIRouter(prefix="/api/quotation-templates", tags=["quotations"])
class TemplateCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    plan_id: int
    validity_days: int = Field(default=30, ge=1, le=365)
class QuotationCreate(BaseModel):
    customer_id: int
    template_id: int

@templates_router.get("")
def list_templates(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "staff"))):
    return [{"id": t.id, "name": t.name, "plan_id": t.plan_id, "plan": t.plan.name, "validity_days": t.validity_days} for t in db.scalars(select(QuotationTemplate).where(QuotationTemplate.is_active.is_(True)))]
@templates_router.post("", status_code=201)
def create_template(data: TemplateCreate, db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    if not db.get(Plan, data.plan_id): raise HTTPException(status_code=404, detail="Plan not found")
    template = QuotationTemplate(**data.model_dump())
    db.add(template); db.commit(); db.refresh(template)
    return {"id": template.id, "name": template.name, "validity_days": template.validity_days}

@router.get("")
def list_quotations(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(Quotation).order_by(Quotation.created_at.desc())
    if user.role == "customer": query = query.where(Quotation.customer_id == user.id)
    return [{"id": q.id, "number": q.number, "customer_id": q.customer_id, "plan": q.plan.name, "status": q.status, "expires_at": q.expires_at} for q in db.scalars(query)]
@router.post("", status_code=201)
def create_quotation(data: QuotationCreate, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "staff"))):
    customer = db.get(User, data.customer_id)
    template = db.get(QuotationTemplate, data.template_id)
    if not customer or customer.role != "customer": raise HTTPException(status_code=404, detail="Customer not found")
    if not template or not template.is_active: raise HTTPException(status_code=404, detail="Quotation template not found")
    now = datetime.now(timezone.utc)
    quote = Quotation(number=f"QUO-{now:%Y%m%d%H%M%S}-{customer.id}", customer_id=customer.id, template_id=template.id, plan_id=template.plan_id, status="quotation", expires_at=now + timedelta(days=template.validity_days))
    db.add(quote); db.commit(); db.refresh(quote)
    return {"id": quote.id, "number": quote.number, "status": quote.status, "expires_at": quote.expires_at}
@router.post("/{quotation_id}/accept")
def accept_quotation(quotation_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    quote = db.get(Quotation, quotation_id)
    if not quote or (user.role == "customer" and quote.customer_id != user.id): raise HTTPException(status_code=404, detail="Quotation not found")
    expiry = quote.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if quote.status != "quotation" or expiry < datetime.now(timezone.utc): raise HTTPException(status_code=409, detail="Quotation is no longer valid")
    quote.status = "confirmed"
    now = datetime.now(timezone.utc)
    subscription = Subscription(customer_id=quote.customer_id, plan_id=quote.plan_id, status="confirmed", number=f"SUB-{now:%Y%m%d%H%M%S}-{quote.customer_id}", started_at=now, current_period_end=add_interval(now, quote.plan.interval), next_billing_at=now)
    db.add(subscription); db.commit(); db.refresh(subscription)
    return {"quotation_id": quote.id, "status": quote.status, "subscription_id": subscription.id, "subscription_status": subscription.status}
@router.post("/subscriptions/{subscription_id}/activate")
def activate_subscription(subscription_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "staff"))):
    subscription = db.get(Subscription, subscription_id)
    if not subscription: raise HTTPException(status_code=404, detail="Subscription not found")
    if subscription.status != "confirmed": raise HTTPException(status_code=409, detail="Only confirmed subscriptions can be activated")
    now = datetime.now(timezone.utc)
    subscription.status = "active"
    invoice, _ = issue_invoice_for_subscription(db, subscription, now, now=now)
    db.commit()
    return {"id": subscription.id, "status": subscription.status, "invoice": {"id": invoice.id, "number": invoice.number, "amount_due": str(invoice.amount_due), "currency": invoice.currency, "status": invoice.status}}

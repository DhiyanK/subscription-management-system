from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.database import get_db
from backend.app.dependencies import current_user, require_roles
from backend.app.models import Plan, ProductVariant, Subscription, User

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])
class SubscriptionCreate(BaseModel):
    plan_id: int
    quantity: int = Field(default=1, ge=1, le=10000)
    customer_id: int | None = None
    variant_id: int | None = None

@router.get("")
def list_subscriptions(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(Subscription).order_by(Subscription.started_at.desc())
    if user.role == "customer":
        query = query.where(Subscription.customer_id == user.id)
    return [{"id": s.id, "number": s.number, "next_billing_at": s.next_billing_at, "customer_id": s.customer_id, "customer_name": s.customer.full_name, "customer_email": s.customer.email, "plan_id": s.plan_id, "plan": s.plan.name, "status": s.status, "started_at": s.started_at, "current_period_end": s.current_period_end, "cancel_at_period_end": s.cancel_at_period_end} for s in db.scalars(query)]

@router.post("", status_code=201)
def create_subscription(data: SubscriptionCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "staff"))):
    plan = db.get(Plan, data.plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(status_code=404, detail="Plan not found or inactive")
    now = datetime.now(timezone.utc)
    if data.quantity < plan.minimum_quantity:
        raise HTTPException(status_code=422, detail=f"Minimum quantity for this plan is {plan.minimum_quantity}")
    available_from = plan.available_from
    available_until = plan.available_until
    if available_from and available_from.tzinfo is None:
        available_from = available_from.replace(tzinfo=timezone.utc)
    if available_until and available_until.tzinfo is None:
        available_until = available_until.replace(tzinfo=timezone.utc)
    if (available_from and now < available_from) or (available_until and now > available_until):
        raise HTTPException(status_code=409, detail="Plan is not currently available")
    months = {"daily": 1 / 30, "weekly": 7 / 30, "monthly": 1, "yearly": 12}.get(plan.interval, 1)
    if not data.customer_id:
        raise HTTPException(status_code=422, detail="Select a customer for this subscription")
    customer = db.get(User, data.customer_id)
    if not customer or customer.role != "customer":
        raise HTTPException(status_code=404, detail="Customer not found")
    customer_id = customer.id
    variant = db.get(ProductVariant, data.variant_id) if data.variant_id else None
    if data.variant_id and (not variant or variant.product_id != plan.product_id):
        raise HTTPException(status_code=422, detail="Selected variant does not belong to this plan product")
    if variant and plan.price + variant.price_adjustment < 0:
        raise HTTPException(status_code=422, detail="Variant adjustment cannot make the subscription price negative")
    subscription = Subscription(customer_id=customer_id, plan_id=plan.id, variant_id=data.variant_id, status="active", number=f"SUB-{now:%Y%m%d%H%M%S}-{user.id}", quantity=data.quantity, started_at=now, next_billing_at=now, current_period_end=now + timedelta(days=max(1, int(30 * months))))
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return {"id": subscription.id, "status": subscription.status, "plan": plan.name, "current_period_end": subscription.current_period_end}

@router.post("/{subscription_id}/cancel")
def cancel_subscription(subscription_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    subscription = db.get(Subscription, subscription_id)
    if not subscription or (user.role == "customer" and subscription.customer_id != user.id):
        raise HTTPException(status_code=404, detail="Subscription not found")
    if subscription.status != "active":
        raise HTTPException(status_code=409, detail="Only active subscriptions can be cancelled")
    subscription.cancel_at_period_end = True
    db.commit()
    return {"id": subscription.id, "status": "active", "cancel_at_period_end": True, "ends_at": subscription.current_period_end}

@router.post("/{subscription_id}/reactivate")
def reactivate_subscription(subscription_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    subscription = db.get(Subscription, subscription_id)
    if not subscription or (user.role == "customer" and subscription.customer_id != user.id):
        raise HTTPException(status_code=404, detail="Subscription not found")
    if subscription.status != "active" or not subscription.cancel_at_period_end:
        raise HTTPException(status_code=409, detail="Subscription is not scheduled for cancellation")
    subscription.cancel_at_period_end = False
    db.commit()
    return {"id": subscription.id, "status": "active", "cancel_at_period_end": False}

@router.post("/{subscription_id}/pause")
def pause_subscription(subscription_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    subscription = db.get(Subscription, subscription_id)
    if not subscription or (user.role == "customer" and subscription.customer_id != user.id):
        raise HTTPException(status_code=404, detail="Subscription not found")
    if not subscription.plan.allow_pause or subscription.status != "active":
        raise HTTPException(status_code=409, detail="This plan does not allow pausing")
    subscription.status = "paused"
    db.commit()
    return {"id": subscription.id, "status": subscription.status}

@router.post("/{subscription_id}/resume")
def resume_subscription(subscription_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    subscription = db.get(Subscription, subscription_id)
    if not subscription or (user.role == "customer" and subscription.customer_id != user.id):
        raise HTTPException(status_code=404, detail="Subscription not found")
    if subscription.status != "paused":
        raise HTTPException(status_code=409, detail="Subscription is not paused")
    subscription.status = "active"
    db.commit()
    return {"id": subscription.id, "status": subscription.status}

@router.post("/{subscription_id}/close")
def close_subscription(subscription_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    subscription = db.get(Subscription, subscription_id)
    if not subscription or (user.role == "customer" and subscription.customer_id != user.id):
        raise HTTPException(status_code=404, detail="Subscription not found")
    if subscription.status not in {"active", "paused"}:
        raise HTTPException(status_code=409, detail="Subscription cannot be closed from its current state")
    period_end = subscription.current_period_end
    if period_end.tzinfo is None:
        period_end = period_end.replace(tzinfo=timezone.utc)
    if not subscription.plan.allow_early_close and period_end > datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="This plan does not allow early closure")
    subscription.status = "closed"
    subscription.cancel_at_period_end = False
    db.commit()
    return {"id": subscription.id, "status": subscription.status}

@router.post("/{subscription_id}/renew", status_code=201)
def renew_subscription(subscription_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    previous = db.get(Subscription, subscription_id)
    if not previous or (user.role == "customer" and previous.customer_id != user.id):
        raise HTTPException(status_code=404, detail="Subscription not found")
    if previous.status != "closed":
        raise HTTPException(status_code=409, detail="Only closed subscriptions can be renewed")
    if not previous.plan.is_active:
        raise HTTPException(status_code=409, detail="This plan is no longer available for renewal")
    now = datetime.now(timezone.utc)
    available_until = previous.plan.available_until
    if available_until and available_until.tzinfo is None:
        available_until = available_until.replace(tzinfo=timezone.utc)
    if available_until and now > available_until:
        raise HTTPException(status_code=409, detail="This plan is outside its renewal availability dates")
    renewed = Subscription(customer_id=previous.customer_id, plan_id=previous.plan_id, variant_id=previous.variant_id, quantity=previous.quantity, status="active", number=f"SUB-{now:%Y%m%d%H%M%S}-{previous.customer_id}", started_at=now, next_billing_at=now, current_period_end=now + timedelta(days=30))
    db.add(renewed); db.commit(); db.refresh(renewed)
    return {"id": renewed.id, "number": renewed.number, "status": renewed.status}

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from backend.app.models import Discount, Invoice, InvoiceLine, Subscription, TaxRule
from backend.app.services.billing_dates import add_interval


def generate_due_invoices(db: Session, now: datetime | None = None) -> int:
    """Create at most one invoice per subscription billing period; safe to rerun."""
    now = now or datetime.now(timezone.utc)
    due = db.scalars(select(Subscription).options(joinedload(Subscription.plan)).where(
        Subscription.status == "active", Subscription.next_billing_at <= now
    ).order_by(Subscription.next_billing_at).with_for_update(skip_locked=True)).all()
    created = 0
    for subscription in due:
        billing_at = subscription.next_billing_at
        period_end = subscription.current_period_end
        if period_end.tzinfo is None:
            period_end = period_end.replace(tzinfo=timezone.utc)
        if subscription.cancel_at_period_end and period_end <= now:
            subscription.status = "closed"
            subscription.closed_at = period_end
            subscription.cancel_at_period_end = False
            if not subscription.cancellation_reason:
                subscription.cancellation_reason = "unspecified"
            continue
        if subscription.plan.available_until:
            plan_end = subscription.plan.available_until
            if plan_end.tzinfo is None:
                plan_end = plan_end.replace(tzinfo=timezone.utc)
            if subscription.plan.auto_close and plan_end <= now:
                subscription.status = "closed"
                subscription.closed_at = plan_end
                subscription.cancel_at_period_end = False
                if not subscription.cancellation_reason:
                    subscription.cancellation_reason = "other"
                continue
        period_key = billing_at.strftime("%Y-%m-%d")
        exists = db.scalar(select(Invoice.id).where(Invoice.subscription_id == subscription.id, Invoice.period_key == period_key))
        if exists:
            subscription.next_billing_at = add_interval(billing_at, subscription.plan.interval)
            continue
        unit_price = subscription.plan.price + (subscription.variant.price_adjustment if subscription.variant else Decimal("0"))
        subtotal = (unit_price * subscription.quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        discount = db.scalar(select(Discount).where(Discount.active.is_(True), Discount.starts_at.is_(None) | (Discount.starts_at <= now), Discount.ends_at.is_(None) | (Discount.ends_at >= now), (Discount.usage_limit.is_(None) | (Discount.usage_count < Discount.usage_limit)), Discount.minimum_quantity <= subscription.quantity, Discount.minimum_amount <= subtotal, (Discount.product_id.is_(None) | (Discount.product_id == subscription.plan.product_id))).order_by(Discount.id).limit(1))
        if discount:
            if discount.percent_off:
                subtotal = (subtotal * (Decimal("100") - discount.percent_off) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                subtotal = max(Decimal("0"), subtotal - discount.amount_off)
            discount.usage_count += 1
        tax_rate = db.scalar(select(func.coalesce(func.sum(TaxRule.rate_percent), 0)).where(TaxRule.is_active.is_(True))) or Decimal("0")
        tax_amount = (subtotal * Decimal(str(tax_rate)) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        invoice = Invoice(subscription_id=subscription.id, number=f"INV-{subscription.id}-{period_key.replace('-', '')}", period_key=period_key, subtotal=subtotal, tax_amount=tax_amount, amount_due=subtotal + tax_amount, currency=subscription.plan.currency, status="open", due_at=billing_at)
        db.add(invoice)
        db.flush()
        line_unit_price = (subtotal / subscription.quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        db.add(InvoiceLine(invoice_id=invoice.id, description=(subscription.plan.product.name if subscription.plan.product else subscription.plan.name), quantity=subscription.quantity, unit_price=line_unit_price, tax_rate=Decimal(str(tax_rate)), line_total=subtotal + tax_amount))
        next_billing = add_interval(billing_at, subscription.plan.interval)
        subscription.next_billing_at = next_billing
        subscription.current_period_end = next_billing
        created += 1
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Concurrent worker inserted a period already; a later run will advance remaining due rows.
        return 0
    return created

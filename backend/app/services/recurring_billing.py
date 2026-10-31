from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from backend.app.models import Discount, Invoice, InvoiceLine, Subscription
from backend.app.services.billing_dates import add_interval


def issue_invoice_for_subscription(
    db: Session,
    subscription: Subscription,
    billing_at: datetime,
    now: datetime | None = None,
) -> tuple[Invoice, bool]:
    """Issue the invoice for one subscription period without committing.

    The unique subscription/period key makes the operation safe to repeat. The
    subscription schedule advances only when a new invoice is issued.
    """
    period_key = billing_at.strftime("%Y-%m-%d")
    existing = db.scalar(
        select(Invoice).where(
            Invoice.subscription_id == subscription.id,
            Invoice.period_key == period_key,
        )
    )
    if existing:
        return existing, False

    now = now or datetime.now(timezone.utc)
    plan = subscription.plan
    unit_price = plan.price + (
        subscription.variant.price_adjustment
        if subscription.variant
        else Decimal("0")
    )
    subtotal = (unit_price * subscription.quantity).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    discount = db.scalar(
        select(Discount)
        .where(
            Discount.active.is_(True),
            (Discount.starts_at.is_(None) | (Discount.starts_at <= now)),
            (Discount.ends_at.is_(None) | (Discount.ends_at >= now)),
            (Discount.usage_limit.is_(None) | (Discount.usage_count < Discount.usage_limit)),
            Discount.minimum_quantity <= subscription.quantity,
            Discount.minimum_amount <= subtotal,
            (Discount.product_id.is_(None) | (Discount.product_id == plan.product_id)),
        )
        .order_by(Discount.id)
        .limit(1)
    )
    if discount:
        if discount.percent_off:
            subtotal = (
                subtotal
                * (Decimal("100") - discount.percent_off)
                / Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            subtotal = max(Decimal("0"), subtotal - discount.amount_off).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        discount.usage_count += 1

    tax_rate = subscription.tax_rate_percent or Decimal("0")
    tax_amount = (
        subtotal * Decimal(str(tax_rate)) / Decimal("100")
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    invoice = Invoice(
        subscription_id=subscription.id,
        number=f"INV-{subscription.id}-{period_key.replace('-', '')}",
        period_key=period_key,
        subtotal=subtotal,
        tax_amount=tax_amount,
        amount_due=subtotal + tax_amount,
        currency=plan.currency,
        status="open",
        due_at=billing_at,
    )
    db.add(invoice)
    db.flush()

    line_unit_price = (subtotal / subscription.quantity).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    db.add(
        InvoiceLine(
            invoice_id=invoice.id,
            description=plan.product.name if plan.product else plan.name,
            quantity=subscription.quantity,
            unit_price=line_unit_price,
            tax_rate=Decimal(str(tax_rate)),
            line_total=subtotal + tax_amount,
        )
    )
    next_billing = add_interval(billing_at, plan.interval)
    subscription.next_billing_at = next_billing
    subscription.current_period_end = next_billing
    return invoice, True


def generate_due_invoices(db: Session, now: datetime | None = None) -> int:
    """Create at most one invoice per subscription billing period; safe to rerun."""
    now = now or datetime.now(timezone.utc)
    due = db.scalars(
        select(Subscription)
        .options(joinedload(Subscription.plan))
        .where(
            Subscription.status == "active",
            Subscription.next_billing_at <= now,
        )
        .order_by(Subscription.next_billing_at)
        .with_for_update(skip_locked=True)
    ).all()
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

        invoice, was_created = issue_invoice_for_subscription(
            db, subscription, billing_at, now=now
        )
        if was_created:
            created += 1
        else:
            next_billing = add_interval(billing_at, subscription.plan.interval)
            subscription.next_billing_at = next_billing
            subscription.current_period_end = next_billing

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # A concurrent worker may have inserted the same period first; another
        # run will safely pick up the remaining due subscriptions.
        return 0
    return created

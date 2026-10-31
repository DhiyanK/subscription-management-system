"""Reusable subscription analytics calculations.

All monetary analytics use the subscription's recurring list amount after the
same recurring discount rules that the invoice runner applies. Taxes are
excluded from MRR/ARR/ARPU because they are pass-through amounts; invoice and
collected revenue reporting use the invoice gross amount including tax.

The billing interval is normalised to a month using:

* daily  -> amount * 365 / 12
* weekly -> amount * 52 / 12
* monthly -> amount
* yearly -> amount / 12

MRR is a current-state metric. Date ranges apply to event metrics (revenue,
growth, churn, renewals and forecast). Plan, product and customer filters apply
wherever the data model has that dimension.
"""
from __future__ import annotations

from calendar import month_name, monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Sequence

from sqlalchemy import Integer, and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models import (
    AnalyticsSnapshot,
    Discount,
    Invoice,
    Payment,
    Plan,
    Product,
    ProductVariant,
    Subscription,
    User,
)

MONEY = Decimal("0.01")
ZERO = Decimal("0")
ACTIVE_STATUS = "active"
RECURRING_STATUSES = ("active", "paused")
OPEN_INVOICE_STATUSES = ("draft", "open", "confirmed")
EXCLUDED_INVOICE_STATUSES = ("paid", "cancelled")
CANCELLATION_REASONS = (
    "too_expensive",
    "no_longer_needed",
    "switching_provider",
    "missing_features",
    "business_closed",
    "other",
    "unspecified",
)


@dataclass(frozen=True)
class AnalyticsFilters:
    """Validated analytics window. ``end`` is exclusive."""

    start: datetime
    end: datetime
    granularity: str = "month"
    plan_id: int | None = None
    product_id: int | None = None
    customer_id: int | None = None

    @property
    def start_date(self) -> date:
        return self.start.date()

    @property
    def end_date(self) -> date:
        return (self.end - timedelta(microseconds=1)).date()

    @property
    def scope(self) -> dict[str, Any]:
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "granularity": self.granularity,
            "plan_id": self.plan_id,
            "product_id": self.product_id,
            "customer_id": self.customer_id,
        }


def default_filters(
    start_date: date | None = None,
    end_date: date | None = None,
    granularity: str = "month",
    plan_id: int | None = None,
    product_id: int | None = None,
    customer_id: int | None = None,
    now: datetime | None = None,
) -> AnalyticsFilters:
    now = now or datetime.now(timezone.utc)
    today = now.date()
    if end_date is None:
        end_date = today
    if start_date is None:
        month_index = today.month - 11
        year = today.year + (month_index - 1) // 12
        month = (month_index - 1) % 12 + 1
        start_date = date(year, month, 1)
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    if (end_date - start_date).days > 366 * 10:
        raise ValueError("Analytics date range cannot exceed 10 years")
    start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return AnalyticsFilters(
        start=start,
        end=end,
        granularity=granularity,
        plan_id=plan_id,
        product_id=product_id,
        customer_id=customer_id,
    )


# ---------------------------------------------------------------------------
# Small date / decimal helpers
# ---------------------------------------------------------------------------


def _money(value: Any) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _decimal(value: Any) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value))


def _f(value: Decimal | int | float | None) -> float:
    return float(value or 0)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def _month_end(value: date) -> date:
    return date(value.year, value.month, monthrange(value.year, value.month)[1])


def _month_bounds(month: date) -> tuple[datetime, datetime]:
    start = datetime.combine(_month_start(month), time.min, tzinfo=timezone.utc)
    end = datetime.combine(_next_month(_month_start(month)), time.min, tzinfo=timezone.utc)
    return start, end


def _bucket_key(value: date, granularity: str) -> str:
    if granularity == "year":
        return f"{value.year:04d}"
    if granularity == "quarter":
        return f"{value.year:04d}-Q{((value.month - 1) // 3) + 1}"
    return f"{value.year:04d}-{value.month:02d}"


def _bucket_label(key: str, granularity: str) -> str:
    if granularity == "year":
        return key
    if granularity == "quarter":
        year, quarter = key.split("-Q")
        return f"{quarter} {year}"
    year, month = key.split("-")
    return f"{month_name[int(month)][:3]} {year}"


def _bucket_start(value: date, granularity: str) -> date:
    if granularity == "year":
        return date(value.year, 1, 1)
    if granularity == "quarter":
        return date(value.year, ((value.month - 1) // 3) * 3 + 1, 1)
    return _month_start(value)


def _next_bucket(value: date, granularity: str) -> date:
    if granularity == "year":
        return date(value.year + 1, 1, 1)
    if granularity == "quarter":
        return date(value.year + (value.month == 10), 1 if value.month == 10 else value.month + 3, 1)
    return _next_month(value)


def period_buckets(filters: AnalyticsFilters) -> list[dict[str, Any]]:
    """Return every period in the selected range, including empty periods."""
    buckets: list[dict[str, Any]] = []
    cursor = _bucket_start(filters.start_date, filters.granularity)
    last = _bucket_start(filters.end_date, filters.granularity)
    guard = 0
    while cursor <= last and guard < 500:
        nxt = _next_bucket(cursor, filters.granularity)
        key = _bucket_key(cursor, filters.granularity)
        buckets.append(
            {
                "key": key,
                "label": _bucket_label(key, filters.granularity),
                "start": cursor.isoformat(),
                "end": (nxt - timedelta(days=1)).isoformat(),
            }
        )
        cursor = nxt
        guard += 1
    return buckets


# ---------------------------------------------------------------------------
# Query scopes
# ---------------------------------------------------------------------------


def _subscription_scope(filters: AnalyticsFilters) -> list[Any]:
    conditions: list[Any] = []
    if filters.plan_id is not None:
        conditions.append(Subscription.plan_id == filters.plan_id)
    if filters.product_id is not None:
        conditions.append(Plan.product_id == filters.product_id)
    if filters.customer_id is not None:
        conditions.append(Subscription.customer_id == filters.customer_id)
    return conditions


def _event_scope(filters: AnalyticsFilters) -> list[Any]:
    """Scope for queries already joined to Plan/Subscription."""
    return _subscription_scope(filters)


def _payment_stmt(filters: AnalyticsFilters):
    return (
        select(Payment, Invoice, Subscription)
        .select_from(Payment)
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .join(Subscription, Invoice.subscription_id == Subscription.id)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(
            Payment.status == "succeeded",
            Payment.created_at >= filters.start,
            Payment.created_at < filters.end,
            Invoice.status != "cancelled",
            *_event_scope(filters),
        )
    )


# ---------------------------------------------------------------------------
# Recurring amount calculation (MRR building block)
# ---------------------------------------------------------------------------


def _discount_for(
    quantity: int,
    subtotal: Decimal,
    product_id: int | None,
    discounts: Sequence[Discount],
    now: datetime,
) -> Discount | None:
    for discount in discounts:
        starts_at = _as_aware(discount.starts_at)
        ends_at = _as_aware(discount.ends_at)
        if starts_at and now < starts_at:
            continue
        if ends_at and now > ends_at:
            continue
        if discount.usage_limit is not None and discount.usage_count >= discount.usage_limit:
            continue
        if discount.minimum_quantity > quantity:
            continue
        if _decimal(discount.minimum_amount) > subtotal:
            continue
        if discount.product_id is not None and discount.product_id != product_id:
            continue
        return discount
    return None


def _periodic_totals(
    db: Session,
    filters: AnalyticsFilters,
    conditions: Iterable[Any],
    *,
    group_customer: bool = False,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Load grouped active/closed recurring values and normalise to monthly.

    Grouping by price-affecting columns keeps this bounded by the number of
    distinct plan/variant/quantity combinations rather than by subscription
    rows. Grouping by customer is only requested for customer analytics.
    """
    now = now or datetime.now(timezone.utc)
    extra = [Subscription.customer_id.label("customer_id")] if group_customer else []
    stmt = (
        select(
            Plan.id.label("plan_id"),
            Plan.name.label("plan_name"),
            Plan.interval.label("interval"),
            Plan.price.label("price"),
            Plan.product_id.label("product_id"),
            Subscription.quantity.label("quantity"),
            func.coalesce(ProductVariant.price_adjustment, 0).label("variant_adjustment"),
            func.count(Subscription.id).label("subscription_count"),
            *extra,
        )
        .select_from(Subscription)
        .join(Plan, Subscription.plan_id == Plan.id)
        .outerjoin(ProductVariant, Subscription.variant_id == ProductVariant.id)
        .where(*conditions)
        .group_by(
            Plan.id,
            Plan.name,
            Plan.interval,
            Plan.price,
            Plan.product_id,
            Subscription.quantity,
            ProductVariant.price_adjustment,
            *extra,
        )
    )
    rows = db.execute(stmt).all()
    discounts = list(
        db.scalars(select(Discount).where(Discount.active.is_(True)).order_by(Discount.id))
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        quantity = int(row.quantity or 1)
        unit = _decimal(row.price) + _decimal(row.variant_adjustment)
        subtotal = (unit * quantity).quantize(MONEY, rounding=ROUND_HALF_UP)
        discount = _discount_for(quantity, subtotal, row.product_id, discounts, now)
        if discount:
            if _decimal(discount.percent_off) > 0:
                subtotal = (
                    subtotal * (Decimal("100") - _decimal(discount.percent_off)) / Decimal("100")
                ).quantize(MONEY, rounding=ROUND_HALF_UP)
            elif _decimal(discount.amount_off) > 0:
                subtotal = max(ZERO, subtotal - _decimal(discount.amount_off)).quantize(
                    MONEY, rounding=ROUND_HALF_UP
                )
        interval = row.interval or "monthly"
        if interval == "daily":
            monthly = subtotal * Decimal("365") / Decimal("12")
        elif interval == "weekly":
            monthly = subtotal * Decimal("52") / Decimal("12")
        elif interval == "yearly":
            monthly = subtotal / Decimal("12")
        else:
            monthly = subtotal
        count = int(row.subscription_count or 0)
        result.append(
            {
                "plan_id": int(row.plan_id),
                "plan_name": row.plan_name,
                "interval": interval,
                "quantity": quantity,
                "subscription_count": count,
                "monthly_unit": _money(monthly),
                "monthly_total": _money(monthly * count),
                "discount_code": discount.code if discount else None,
                "customer_id": int(row.customer_id) if group_customer and row.customer_id else None,
            }
        )
    return result


def _sum_rows(rows: Iterable[dict[str, Any]]) -> Decimal:
    return sum((_decimal(row["monthly_total"]) for row in rows), ZERO)


def current_mrr_rows(db: Session, filters: AnalyticsFilters, *, group_customer: bool = False) -> list[dict[str, Any]]:
    return _periodic_totals(
        db,
        filters,
        [Subscription.status == ACTIVE_STATUS, *_subscription_scope(filters)],
        group_customer=group_customer,
    )


def current_mrr(db: Session, filters: AnalyticsFilters) -> Decimal:
    return _money(_sum_rows(current_mrr_rows(db, filters)))


def historical_mrr_rows(db: Session, filters: AnalyticsFilters, at: datetime) -> list[dict[str, Any]]:
    """Reconstruct MRR at an instant from start/closure dates.

    The database does not retain plan-price history, so this reconstruction uses
    the current plan/variant price. It is labelled as reconstructed in API
    metadata and is superseded by a captured snapshot when one exists.
    """
    return _periodic_totals(
        db,
        filters,
        [
            Subscription.started_at < at,
            or_(
                and_(
                    Subscription.closed_at.is_(None),
                    Subscription.status.in_(["active", "paused"]),
                ),
                Subscription.closed_at >= at,
            ),
            *_subscription_scope(filters),
        ],
    )


def historical_mrr(db: Session, filters: AnalyticsFilters, at: datetime) -> Decimal:
    return _money(_sum_rows(historical_mrr_rows(db, filters, at)))


def historical_active_counts(db: Session, filters: AnalyticsFilters, at: datetime) -> tuple[int, int]:
    subs = (
        select(func.count(Subscription.id))
        .select_from(Subscription)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(
            Subscription.started_at < at,
            or_(
                and_(
                    Subscription.closed_at.is_(None),
                    Subscription.status.in_(["active", "paused"]),
                ),
                Subscription.closed_at >= at,
            ),
            *_event_scope(filters),
        )
    )
    customers = (
        select(func.count(func.distinct(Subscription.customer_id)))
        .select_from(Subscription)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(
            Subscription.started_at < at,
            or_(
                and_(
                    Subscription.closed_at.is_(None),
                    Subscription.status.in_(["active", "paused"]),
                ),
                Subscription.closed_at >= at,
            ),
            *_event_scope(filters),
        )
    )
    return int(db.scalar(subs) or 0), int(db.scalar(customers) or 0)


# ---------------------------------------------------------------------------
# Revenue
# ---------------------------------------------------------------------------


def _grouped_month_totals(stmt) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for row in stmt:
        year = int(row.year)
        month = int(row.month)
        key = f"{year:04d}-{month:02d}"
        totals[key] = totals.get(key, ZERO) + _decimal(row.total)
    return totals


def _collected_by_month(db: Session, filters: AnalyticsFilters) -> dict[str, Decimal]:
    from sqlalchemy import Integer

    stmt = (
        select(
            func.cast(func.extract("year", Payment.created_at), Integer).label("year"),
            func.cast(func.extract("month", Payment.created_at), Integer).label("month"),
            func.coalesce(func.sum(Payment.amount), 0).label("total"),
        )
        .select_from(Payment)
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .join(Subscription, Invoice.subscription_id == Subscription.id)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(
            Payment.status == "succeeded",
            Payment.created_at >= filters.start,
            Payment.created_at < filters.end,
            Invoice.status != "cancelled",
            *_event_scope(filters),
        )
        .group_by("year", "month")
    )
    return _grouped_month_totals(db.execute(stmt))


def _invoiced_by_month(db: Session, filters: AnalyticsFilters) -> dict[str, Decimal]:
    from sqlalchemy import Integer

    stmt = (
        select(
            func.cast(func.extract("year", Invoice.created_at), Integer).label("year"),
            func.cast(func.extract("month", Invoice.created_at), Integer).label("month"),
            func.coalesce(func.sum(Invoice.amount_due), 0).label("total"),
        )
        .select_from(Invoice)
        .join(Subscription, Invoice.subscription_id == Subscription.id)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(
            Invoice.created_at >= filters.start,
            Invoice.created_at < filters.end,
            Invoice.status != "cancelled",
            *_event_scope(filters),
        )
        .group_by("year", "month")
    )
    return _grouped_month_totals(db.execute(stmt))


def _merge_month_totals(totals: dict[str, Decimal], filters: AnalyticsFilters) -> dict[str, Decimal]:
    merged: dict[str, Decimal] = {}
    for key, amount in totals.items():
        y, m = key.split("-")
        bucket = _bucket_key(date(int(y), int(m), 1), filters.granularity)
        merged[bucket] = merged.get(bucket, ZERO) + amount
    return merged


def revenue_series(db: Session, filters: AnalyticsFilters) -> dict[str, Any]:
    collected_months = _merge_month_totals(_collected_by_month(db, filters), filters)
    invoiced_months = _merge_month_totals(_invoiced_by_month(db, filters), filters)
    series = []
    for bucket in period_buckets(filters):
        collected = _money(collected_months.get(bucket["key"], ZERO))
        invoiced = _money(invoiced_months.get(bucket["key"], ZERO))
        series.append(
            {
                **bucket,
                "collected": _f(collected),
                "invoiced": _f(invoiced),
            }
        )
    return {
        "series": series,
        "total_collected": _f(sum((_decimal(item["collected"]) for item in series), ZERO)),
        "total_invoiced": _f(sum((_decimal(item["invoiced"]) for item in series), ZERO)),
        "granularity": filters.granularity,
        "currency": "INR",
        "basis": {
            "collected": "Succeeded payments by payment date; cancelled invoices excluded.",
            "invoiced": "Non-cancelled invoices by invoice creation date; gross amount including tax.",
        },
    }


def _collected_total(db: Session, filters: AnalyticsFilters) -> Decimal:
    stmt = (
        select(func.coalesce(func.sum(Payment.amount), 0))
        .select_from(Payment)
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .join(Subscription, Invoice.subscription_id == Subscription.id)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(
            Payment.status == "succeeded",
            Payment.created_at >= filters.start,
            Payment.created_at < filters.end,
            Invoice.status != "cancelled",
            *_event_scope(filters),
        )
    )
    return _money(db.scalar(stmt))


def _breakdown_values(pairs: Iterable[tuple[str | None, Decimal]]) -> list[dict[str, Any]]:
    rows = [(label, _money(amount)) for label, amount in pairs]
    total = sum((amount for _, amount in rows), ZERO)
    return [
        {
            "label": label or "Unassigned",
            "amount": _f(amount),
            "percentage": _f((amount / total * 100) if total else ZERO),
        }
        for label, amount in sorted(rows, key=lambda item: item[1], reverse=True)
    ]


def revenue_breakdown(db: Session, filters: AnalyticsFilters) -> dict[str, Any]:
    base_where = [
        Payment.status == "succeeded",
        Payment.created_at >= filters.start,
        Payment.created_at < filters.end,
        Invoice.status != "cancelled",
        *_event_scope(filters),
    ]
    joins = (
        select(Payment)
        .select_from(Payment)
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .join(Subscription, Invoice.subscription_id == Subscription.id)
        .join(Plan, Subscription.plan_id == Plan.id)
    )
    # One grouped scan feeds all three categorical breakdowns. Grouping by the
    # combination keeps the result bounded by distinct plan/product/method
    # combinations instead of by payment rows.
    product_name = func.coalesce(Product.name, "Unassigned")
    combined = (
        joins.outerjoin(Product, Plan.product_id == Product.id)
        .with_only_columns(
            Plan.name.label("plan"),
            product_name.label("product"),
            Payment.method.label("method"),
            func.sum(Payment.amount).label("total"),
        )
        .where(*base_where)
        .group_by(Plan.name, product_name, Payment.method)
    )
    plan_totals: dict[str, Decimal] = {}
    product_totals: dict[str, Decimal] = {}
    method_totals: dict[str, Decimal] = {}
    for plan_name, product, method, amount in db.execute(combined):
        value = _decimal(amount)
        plan_totals[plan_name] = plan_totals.get(plan_name, ZERO) + value
        product_totals[product] = product_totals.get(product, ZERO) + value
        method_totals[method] = method_totals.get(method, ZERO) + value
    by_plan = _breakdown_values(plan_totals.items())
    by_product = _breakdown_values(product_totals.items())
    by_method = _breakdown_values(method_totals.items())

    # Collected revenue by the customer's current lifecycle segment. This is
    # explicitly "current segment", not the segment at payment time.
    active_customer_ids = set(
        db.scalars(
            select(Subscription.customer_id)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(Subscription.status == ACTIVE_STATUS, *_event_scope(filters))
            .distinct()
        )
    )
    customer_revenue = db.execute(
        joins.with_only_columns(
            Subscription.customer_id.label("customer_id"),
            func.sum(Payment.amount).label("total"),
        )
        .where(*base_where)
        .group_by(Subscription.customer_id)
    ).all()
    segment_totals: dict[str, Decimal] = {"Active customers": ZERO, "Inactive customers": ZERO}
    for customer_id, amount in customer_revenue:
        label = "Active customers" if customer_id in active_customer_ids else "Inactive customers"
        segment_totals[label] += _decimal(amount)
    segment_total = sum(segment_totals.values(), ZERO)
    by_segment = [
        {
            "label": label,
            "amount": _f(amount),
            "percentage": _f((amount / segment_total * 100) if segment_total else ZERO),
        }
        for label, amount in sorted(segment_totals.items(), key=lambda item: item[1], reverse=True)
    ]
    return {
        "by_product": by_product,
        "by_plan": by_plan,
        "by_payment_method": by_method,
        "by_customer_segment": by_segment,
        "currency": "INR",
    }


# ---------------------------------------------------------------------------
# Subscriptions, churn and renewals
# ---------------------------------------------------------------------------


def status_distribution(db: Session, filters: AnalyticsFilters) -> list[dict[str, Any]]:
    stmt = (
        select(Subscription.status, func.count(Subscription.id))
        .select_from(Subscription)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(*_event_scope(filters))
        .group_by(Subscription.status)
    )
    return [
        {"status": status, "count": int(count)}
        for status, count in sorted(db.execute(stmt).all(), key=lambda item: item[0])
    ]


def _started_counts(db: Session, filters: AnalyticsFilters, renewed: bool | None = None) -> dict[str, int]:
    from sqlalchemy import Integer

    conditions = [
        Subscription.started_at >= filters.start,
        Subscription.started_at < filters.end,
        *_event_scope(filters),
    ]
    if renewed is True:
        conditions.append(Subscription.renewed_from_id.is_not(None))
    elif renewed is False:
        conditions.append(Subscription.renewed_from_id.is_(None))
    stmt = (
        select(
            func.cast(func.extract("year", Subscription.started_at), Integer).label("year"),
            func.cast(func.extract("month", Subscription.started_at), Integer).label("month"),
            func.count(Subscription.id).label("count"),
        )
        .select_from(Subscription)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(*conditions)
        .group_by("year", "month")
    )
    result: dict[str, int] = {}
    for row in db.execute(stmt):
        result[f"{int(row.year):04d}-{int(row.month):02d}"] = int(row.count or 0)
    return result


def _closed_counts(db: Session, filters: AnalyticsFilters, reasons: bool | None = None) -> dict[str, int]:
    from sqlalchemy import Integer

    conditions = [
        Subscription.closed_at.is_not(None),
        Subscription.closed_at >= filters.start,
        Subscription.closed_at < filters.end,
        *_event_scope(filters),
    ]
    if reasons is True:
        conditions.append(Subscription.cancellation_reason.is_not(None))
    elif reasons is False:
        conditions.append(Subscription.cancellation_reason.is_(None))
    stmt = (
        select(
            func.cast(func.extract("year", Subscription.closed_at), Integer).label("year"),
            func.cast(func.extract("month", Subscription.closed_at), Integer).label("month"),
            func.count(Subscription.id).label("count"),
        )
        .select_from(Subscription)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(*conditions)
        .group_by("year", "month")
    )
    result: dict[str, int] = {}
    for row in db.execute(stmt):
        result[f"{int(row.year):04d}-{int(row.month):02d}"] = int(row.count or 0)
    return result


def _counts_in_buckets(month_counts: dict[str, int], filters: AnalyticsFilters) -> dict[str, int]:
    merged: dict[str, int] = {}
    for key, count in month_counts.items():
        y, m = key.split("-")
        bucket = _bucket_key(date(int(y), int(m), 1), filters.granularity)
        merged[bucket] = merged.get(bucket, 0) + count
    return merged


def subscription_growth(db: Session, filters: AnalyticsFilters) -> list[dict[str, Any]]:
    new_counts = _counts_in_buckets(_started_counts(db, filters, renewed=False), filters)
    renewed_counts = _counts_in_buckets(_started_counts(db, filters, renewed=True), filters)
    cancelled_counts = _counts_in_buckets(_closed_counts(db, filters, reasons=True), filters)
    closed_counts = _counts_in_buckets(_closed_counts(db, filters, reasons=False), filters)
    return [
        {
            **bucket,
            "new_subscriptions": new_counts.get(bucket["key"], 0),
            "renewed_subscriptions": renewed_counts.get(bucket["key"], 0),
            "cancelled_subscriptions": cancelled_counts.get(bucket["key"], 0),
            "closed_subscriptions": closed_counts.get(bucket["key"], 0),
        }
        for bucket in period_buckets(filters)
    ]


def _active_customer_ids_at(db: Session, filters: AnalyticsFilters, at: datetime) -> set[int]:
    return set(
        db.scalars(
            select(Subscription.customer_id)
            .select_from(Subscription)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(
                Subscription.started_at < at,
                or_(
                    and_(
                        Subscription.closed_at.is_(None),
                        Subscription.status.in_(RECURRING_STATUSES),
                    ),
                    Subscription.closed_at >= at,
                ),
                *_event_scope(filters),
            )
            .distinct()
        )
    )


def _history_reliability(db: Session) -> dict[str, Any]:
    total_closed = int(
        db.scalar(select(func.count(Subscription.id)).where(Subscription.status == "closed")) or 0
    )
    dated_closed = int(
        db.scalar(
            select(func.count(Subscription.id)).where(
                Subscription.status == "closed", Subscription.closed_at.is_not(None)
            )
        )
        or 0
    )
    earliest = db.scalar(select(func.min(Subscription.closed_at)).where(Subscription.closed_at.is_not(None)))
    return {
        "available": total_closed == 0 or dated_closed > 0,
        "closed_subscriptions": total_closed,
        "closed_with_timestamp": dated_closed,
        "reliable_from": _as_aware(earliest).date().isoformat() if earliest else None,
        "note": (
            "Closure timestamps are recorded from this analytics release onward. "
            "Historical closures without a timestamp are not counted in churn or closure trends."
        ),
    }


def churn_metrics(db: Session, filters: AnalyticsFilters) -> dict[str, Any]:
    reliability = _history_reliability(db)
    latest_month = _month_start(filters.end_date)
    month_start, next_month_start = _month_bounds(latest_month)
    period_end = min(next_month_start, filters.end)
    if period_end <= month_start:
        period_end = next_month_start

    beginning_customers = _active_customer_ids_at(db, filters, month_start)
    ending_customers = _active_customer_ids_at(db, filters, period_end)
    lost_customers = beginning_customers - ending_customers

    churn_available = reliability["available"]
    customer_churn = (
        (Decimal(len(lost_customers)) / Decimal(len(beginning_customers)) * 100)
        if churn_available and beginning_customers
        else None
    )

    beginning_subs = int(
        db.scalar(
            select(func.count(Subscription.id))
            .select_from(Subscription)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(
                Subscription.started_at < month_start,
                or_(
                    and_(
                        Subscription.closed_at.is_(None),
                        Subscription.status.in_(RECURRING_STATUSES),
                    ),
                    Subscription.closed_at >= month_start,
                ),
                *_event_scope(filters),
            )
        )
        or 0
    )
    closed_subs = int(
        db.scalar(
            select(func.count(Subscription.id))
            .select_from(Subscription)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(
                Subscription.closed_at.is_not(None),
                Subscription.closed_at >= month_start,
                Subscription.closed_at < period_end,
                *_event_scope(filters),
            )
        )
        or 0
    )
    subscription_churn = (
        (Decimal(closed_subs) / Decimal(beginning_subs) * 100)
        if churn_available and beginning_subs
        else None
    )

    churned_rows = _periodic_totals(
        db,
        filters,
        [
            Subscription.closed_at.is_not(None),
            Subscription.closed_at >= month_start,
            Subscription.closed_at < period_end,
            *_subscription_scope(filters),
        ],
    )
    churned_mrr = _money(_sum_rows(churned_rows))

    # Previous month is used for the KPI comparison only when history supports it.
    previous_start = _month_start(date(month_start.year - (month_start.month == 1), 12 if month_start.month == 1 else month_start.month - 1, 1))
    previous_end = month_start
    previous_begin = _active_customer_ids_at(db, filters, previous_start)
    previous_end_set = _active_customer_ids_at(db, filters, previous_end)
    previous_churn = (
        (Decimal(len(previous_begin - previous_end_set)) / Decimal(len(previous_begin)) * 100)
        if churn_available and previous_begin
        else None
    )
    previous_closed_subs = int(
        db.scalar(
            select(func.count(Subscription.id))
            .select_from(Subscription)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(
                Subscription.closed_at.is_not(None),
                Subscription.closed_at >= previous_start,
                Subscription.closed_at < previous_end,
                *_event_scope(filters),
            )
        )
        or 0
    )
    previous_begin_subs = int(
        db.scalar(
            select(func.count(Subscription.id))
            .select_from(Subscription)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(
                Subscription.started_at < previous_start,
                or_(
                    and_(
                        Subscription.closed_at.is_(None),
                        Subscription.status.in_(RECURRING_STATUSES),
                    ),
                    Subscription.closed_at >= previous_start,
                ),
                *_event_scope(filters),
            )
        )
        or 0
    )
    previous_subscription_churn = (
        (Decimal(previous_closed_subs) / Decimal(previous_begin_subs) * 100)
        if churn_available and previous_begin_subs
        else None
    )

    reasons_stmt = (
        select(
            func.coalesce(Subscription.cancellation_reason, "unspecified").label("reason"),
            func.count(Subscription.id).label("count"),
            func.coalesce(func.sum(Plan.price * Subscription.quantity), 0).label("list_value"),
        )
        .select_from(Subscription)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(
            Subscription.closed_at.is_not(None),
            Subscription.closed_at >= month_start,
            Subscription.closed_at < period_end,
            *_event_scope(filters),
        )
        .group_by(func.coalesce(Subscription.cancellation_reason, "unspecified"))
        .order_by(func.count(Subscription.id).desc())
    )
    reasons = [
        {"reason": reason, "count": int(count), "list_value": _f(_money(value))}
        for reason, count, value in db.execute(reasons_stmt)
    ]

    trend_stmt = (
        select(
            func.cast(func.extract("year", Subscription.closed_at), Integer).label("year"),
            func.cast(func.extract("month", Subscription.closed_at), Integer).label("month"),
            func.count(Subscription.id).label("count"),
        )
        .select_from(Subscription)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(
            Subscription.closed_at.is_not(None),
            Subscription.closed_at >= filters.start,
            Subscription.closed_at < filters.end,
            *_event_scope(filters),
        )
        .group_by("year", "month")
    )
    trend_counts = _counts_in_buckets(
        {
            f"{int(row.year):04d}-{int(row.month):02d}": int(row.count or 0)
            for row in db.execute(trend_stmt)
        },
        filters,
    )
    cancellation_trend = [
        {**bucket, "cancelled": trend_counts.get(bucket["key"], 0)} for bucket in period_buckets(filters)
    ]

    def _round_pct(value: Decimal | None) -> float | None:
        return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if value is not None else None

    return {
        "month": latest_month.isoformat(),
        "available": churn_available,
        "reliability": reliability,
        "customer_churn_rate": _round_pct(customer_churn),
        "subscription_churn_rate": _round_pct(subscription_churn),
        "customers_at_start": len(beginning_customers),
        "customers_lost": len(lost_customers),
        "subscriptions_at_start": beginning_subs,
        "churned_subscriptions": closed_subs,
        "churned_mrr": _f(churned_mrr),
        "previous_customer_churn_rate": _round_pct(previous_churn),
        "previous_subscription_churn_rate": _round_pct(previous_subscription_churn),
        "reasons": reasons,
        "cancellation_trend": cancellation_trend,
        "definition": (
            "Customer churn counts customers active at the start of the month who have no "
            "active or paused subscription by the measurement date and whose subscription "
            "closed in that month. A scheduled cancellation is not churn until it takes effect."
        ),
    }


def upcoming_renewals(
    db: Session,
    filters: AnalyticsFilters,
    *,
    days: int = 30,
    limit: int = 50,
    offset: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    upper = now + timedelta(days=days)
    stmt = (
        select(Subscription, Plan, User, ProductVariant)
        .select_from(Subscription)
        .join(Plan, Subscription.plan_id == Plan.id)
        .join(User, Subscription.customer_id == User.id)
        .outerjoin(ProductVariant, Subscription.variant_id == ProductVariant.id)
        .where(
            Subscription.status == ACTIVE_STATUS,
            Subscription.next_billing_at >= now,
            Subscription.next_billing_at <= upper,
            *_event_scope(filters),
        )
        .order_by(Subscription.next_billing_at)
    )
    rows = db.execute(stmt).all()
    discounts = list(
        db.scalars(select(Discount).where(Discount.active.is_(True)).order_by(Discount.id))
    )
    items = []
    for subscription, plan, customer, variant in rows:
        quantity = subscription.quantity or 1
        unit = _decimal(plan.price) + _decimal(variant.price_adjustment if variant else 0)
        subtotal = (unit * quantity).quantize(MONEY, rounding=ROUND_HALF_UP)
        discount = _discount_for(quantity, subtotal, plan.product_id, discounts, now)
        if discount:
            if _decimal(discount.percent_off) > 0:
                subtotal = (
                    subtotal * (Decimal("100") - _decimal(discount.percent_off)) / Decimal("100")
                ).quantize(MONEY, rounding=ROUND_HALF_UP)
            elif _decimal(discount.amount_off) > 0:
                subtotal = max(ZERO, subtotal - _decimal(discount.amount_off)).quantize(
                    MONEY, rounding=ROUND_HALF_UP
                )
        items.append(
            {
                "subscription_id": subscription.id,
                "number": subscription.number,
                "customer_id": customer.id,
                "customer_name": customer.full_name,
                "customer_email": customer.email,
                "plan_id": plan.id,
                "plan": plan.name,
                "renewal_date": _as_aware(subscription.next_billing_at).isoformat(),
                "amount": _f(subtotal),
                "currency": plan.currency,
                "scheduled_cancellation": subscription.cancel_at_period_end,
            }
        )
    total = len(items)
    return {
        "days": days,
        "count": total,
        "amount": _f(sum((_decimal(item["amount"]) for item in items), ZERO)),
        "items": items[offset : offset + limit],
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


def _customer_ids_with_active_subs(db: Session, filters: AnalyticsFilters) -> set[int]:
    return set(
        db.scalars(
            select(Subscription.customer_id)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(Subscription.status == ACTIVE_STATUS, *_event_scope(filters))
            .distinct()
        )
    )


def customer_growth(db: Session, filters: AnalyticsFilters) -> list[dict[str, Any]]:
    from sqlalchemy import Integer

    conditions = [User.role == "customer"]
    if filters.customer_id is not None:
        conditions.append(User.id == filters.customer_id)
    stmt = (
        select(
            func.cast(func.extract("year", User.created_at), Integer).label("year"),
            func.cast(func.extract("month", User.created_at), Integer).label("month"),
            func.count(User.id).label("count"),
        )
        .where(User.created_at >= filters.start, User.created_at < filters.end, *conditions)
        .group_by("year", "month")
    )
    counts: dict[str, int] = {}
    for row in db.execute(stmt):
        counts[f"{int(row.year):04d}-{int(row.month):02d}"] = int(row.count or 0)
    merged = _counts_in_buckets(counts, filters)
    return [
        {**bucket, "new_customers": merged.get(bucket["key"], 0)} for bucket in period_buckets(filters)
    ]


def _customer_mrr_map(db: Session, filters: AnalyticsFilters) -> dict[int, Decimal]:
    rows = current_mrr_rows(db, filters, group_customer=True)
    result: dict[int, Decimal] = {}
    for row in rows:
        customer_id = row["customer_id"]
        if customer_id is None:
            continue
        result[customer_id] = result.get(customer_id, ZERO) + _decimal(row["monthly_total"])
    return result


def _overdue_customer_ids(db: Session, filters: AnalyticsFilters, now: datetime) -> set[int]:
    return set(
        db.scalars(
            select(Subscription.customer_id)
            .join(Invoice, Invoice.subscription_id == Subscription.id)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(
                Invoice.status.in_(["open", "confirmed"]),
                Invoice.due_at < now,
                *_event_scope(filters),
            )
            .distinct()
        )
    )


def _ending_soon_customer_ids(db: Session, filters: AnalyticsFilters, now: datetime, days: int = 30) -> set[int]:
    return set(
        db.scalars(
            select(Subscription.customer_id)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(
                Subscription.status == ACTIVE_STATUS,
                Subscription.current_period_end >= now,
                Subscription.current_period_end <= now + timedelta(days=days),
                *_event_scope(filters),
            )
            .distinct()
        )
    )


def _scheduled_cancel_customer_ids(db: Session, filters: AnalyticsFilters) -> set[int]:
    return set(
        db.scalars(
            select(Subscription.customer_id)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(
                Subscription.status == ACTIVE_STATUS,
                Subscription.cancel_at_period_end.is_(True),
                *_event_scope(filters),
            )
            .distinct()
        )
    )


def segmentation(db: Session, filters: AnalyticsFilters, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    total_customers = int(
        db.scalar(
            select(func.count(User.id)).where(
                User.role == "customer",
                *([User.id == filters.customer_id] if filters.customer_id is not None else []),
            )
        )
        or 0
    )
    mrr_map = _customer_mrr_map(db, filters)
    active_ids = set(mrr_map)
    customer_count = (
        int(db.scalar(select(func.count(User.id)).where(User.role == "customer", User.id == filters.customer_id)) or 0)
        if filters.customer_id is not None
        else total_customers
    )
    active_mrr = sum(mrr_map.values(), ZERO)
    arpu = active_mrr / len(active_ids) if active_ids else ZERO
    high_value_threshold = arpu * 2
    high_value_ids = {
        customer_id for customer_id, amount in mrr_map.items() if amount >= high_value_threshold and amount > 0
    }
    overdue_ids = _overdue_customer_ids(db, filters, now)
    ending_ids = _ending_soon_customer_ids(db, filters, now)
    scheduled_ids = _scheduled_cancel_customer_ids(db, filters)
    inactive_count = max(0, customer_count - len(active_ids))
    return {
        "total_customers": customer_count,
        "active_customers": len(active_ids),
        "inactive_customers": inactive_count,
        "high_value_customers": len(high_value_ids),
        "overdue_customers": len(overdue_ids),
        "ending_soon_customers": len(ending_ids),
        "scheduled_cancellation_customers": len(scheduled_ids),
        "high_value_threshold": _f(_money(high_value_threshold)),
        "high_value_definition": "Customers whose current MRR is at least twice the active-customer ARPU.",
        "segments": [
            {"key": "active", "label": "Active customers", "count": len(active_ids)},
            {"key": "inactive", "label": "Inactive customers", "count": inactive_count},
            {"key": "high_value", "label": "High-value customers", "count": len(high_value_ids)},
            {"key": "overdue", "label": "Overdue invoices", "count": len(overdue_ids)},
            {"key": "ending_soon", "label": "Ending in 30 days", "count": len(ending_ids)},
            {"key": "scheduled_cancellation", "label": "Scheduled cancellations", "count": len(scheduled_ids)},
        ],
        "_ids": {
            "active": active_ids,
            "inactive": None,
            "high_value": high_value_ids,
            "overdue": overdue_ids,
            "ending_soon": ending_ids,
            "scheduled_cancellation": scheduled_ids,
        },
        "_mrr": mrr_map,
        "_arpu": arpu,
    }


def customer_analytics(db: Session, filters: AnalyticsFilters, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    growth = customer_growth(db, filters)
    seg = segmentation(db, filters, now)
    churn = churn_metrics(db, filters)
    active_mrr = current_mrr(db, filters)
    active_customers = seg["active_customers"]
    arpu = active_mrr / active_customers if active_customers else ZERO
    customer_churn = churn["customer_churn_rate"]
    clv_available = bool(customer_churn and customer_churn > 0 and active_customers)
    clv = (
        (arpu / (Decimal(str(customer_churn)) / Decimal("100")))
        if clv_available
        else None
    )
    return {
        "total_registered_customers": seg["total_customers"],
        "new_customers_series": growth,
        "new_customers_in_period": sum(item["new_customers"] for item in growth),
        "active_customers": active_customers,
        "customers_without_active_subscriptions": seg["inactive_customers"],
        "segmentation": seg,
        "clv": {
            "estimate": _f(_money(clv)) if clv is not None else None,
            "available": clv_available,
            "methodology": "Estimated CLV = current ARPU / latest monthly customer churn rate.",
            "label": "Estimated, not a guaranteed lifetime value.",
            "unavailable_reason": (
                None
                if clv_available
                else "CLV is unavailable when monthly churn is zero or there is insufficient active-customer history."
            ),
        },
    }


def customer_segment_rows(
    db: Session,
    filters: AnalyticsFilters,
    segment: str,
    *,
    limit: int = 25,
    offset: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    seg = segmentation(db, filters, now)
    if segment not in seg["_ids"]:
        raise ValueError("Unknown customer segment")
    ids = seg["_ids"][segment]
    mrr_map = seg["_mrr"]
    if segment == "inactive":
        active_ids = seg["_ids"]["active"]
        query = select(User).where(User.role == "customer", User.id.not_in(active_ids))
        if filters.customer_id is not None:
            query = query.where(User.id == filters.customer_id)
    else:
        if not ids:
            return {"segment": segment, "count": 0, "items": [], "limit": limit, "offset": offset}
        query = select(User).where(User.id.in_(ids))
    total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    users = list(db.scalars(query.order_by(User.full_name).offset(offset).limit(limit)))
    overdue_ids = seg["_ids"]["overdue"]
    ending_ids = seg["_ids"]["ending_soon"]
    scheduled_ids = seg["_ids"]["scheduled_cancellation"]
    return {
        "segment": segment,
        "count": total,
        "items": [
            {
                "id": user.id,
                "name": user.full_name,
                "email": user.email,
                "mrr": _f(_money(mrr_map.get(user.id, ZERO))),
                "overdue": user.id in overdue_ids,
                "ending_soon": user.id in ending_ids,
                "scheduled_cancellation": user.id in scheduled_ids,
            }
            for user in users
        ],
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


def _plan_subscription_counts(db: Session, filters: AnalyticsFilters) -> dict[int, int]:
    stmt = (
        select(Plan.id, func.count(Subscription.id))
        .select_from(Plan)
        .join(Subscription, Subscription.plan_id == Plan.id)
        .where(*_event_scope(filters))
        .group_by(Plan.id)
    )
    return {int(plan_id): int(count) for plan_id, count in db.execute(stmt)}


def _plan_event_counts(db: Session, filters: AnalyticsFilters, event: str) -> dict[int, int]:
    if event == "new":
        column = Subscription.started_at
    else:
        column = Subscription.closed_at
    stmt = (
        select(Plan.id, func.count(Subscription.id))
        .select_from(Plan)
        .join(Subscription, Subscription.plan_id == Plan.id)
        .where(column >= filters.start, column < filters.end, *_event_scope(filters))
        .group_by(Plan.id)
    )
    return {int(plan_id): int(count) for plan_id, count in db.execute(stmt)}


def plan_performance(
    db: Session,
    filters: AnalyticsFilters,
    *,
    sort: str = "revenue",
) -> dict[str, Any]:
    plan_query = select(Plan)
    if filters.plan_id is not None:
        plan_query = plan_query.where(Plan.id == filters.plan_id)
    if filters.product_id is not None:
        plan_query = plan_query.where(Plan.product_id == filters.product_id)
    plans = {plan.id: plan for plan in db.scalars(plan_query.order_by(Plan.name))}
    active_rows = current_mrr_rows(db, filters)
    mrr_by_plan: dict[int, Decimal] = {}
    active_subs_by_plan: dict[int, int] = {}
    quantity_by_plan: dict[int, int] = {}
    for row in active_rows:
        plan_id = row["plan_id"]
        mrr_by_plan[plan_id] = mrr_by_plan.get(plan_id, ZERO) + _decimal(row["monthly_total"])
        active_subs_by_plan[plan_id] = active_subs_by_plan.get(plan_id, 0) + row["subscription_count"]
        quantity_by_plan[plan_id] = quantity_by_plan.get(plan_id, 0) + row["quantity"] * row["subscription_count"]
    total_subs = _plan_subscription_counts(db, filters)
    new_subs = _plan_event_counts(db, filters, "new")
    closed_subs = _plan_event_counts(db, filters, "closed")
    total_mrr = sum(mrr_by_plan.values(), ZERO)
    items = []
    for plan_id, plan in plans.items():
        active_count = active_subs_by_plan.get(plan_id, 0)
        plan_mrr = _money(mrr_by_plan.get(plan_id, ZERO))
        quantity_total = quantity_by_plan.get(plan_id, 0)
        items.append(
            {
                "plan_id": plan_id,
                "plan": plan.name,
                "interval": plan.interval,
                "is_active": plan.is_active,
                "total_subscribers": total_subs.get(plan_id, 0),
                "active_subscribers": active_count,
                "new_subscriptions": new_subs.get(plan_id, 0),
                "cancelled_subscriptions": closed_subs.get(plan_id, 0),
                "mrr": _f(plan_mrr),
                "arr": _f(_money(plan_mrr * 12)),
                "average_quantity": _f(Decimal(quantity_total) / Decimal(active_count)) if active_count else 0,
                "revenue_contribution_percent": _f((plan_mrr / total_mrr * 100) if total_mrr else ZERO),
            }
        )
    sorters = {
        "revenue": lambda item: item["mrr"],
        "subscribers": lambda item: item["active_subscribers"],
        "growth": lambda item: item["new_subscriptions"],
        "cancellations": lambda item: item["cancelled_subscriptions"],
    }
    items.sort(key=sorters.get(sort, sorters["revenue"]), reverse=True)
    best = sorted(items, key=lambda item: item["mrr"], reverse=True)[:3]
    attention = sorted(items, key=lambda item: (item["cancelled_subscriptions"], -item["new_subscriptions"]), reverse=True)[:3]
    return {
        "plans": items,
        "sort": sort if sort in sorters else "revenue",
        "best_performing": [item for item in best if item["mrr"] > 0 or item["active_subscribers"] > 0],
        "needs_attention": [item for item in attention if item["cancelled_subscriptions"] > 0],
        "currency": "INR",
    }


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------


def _add_interval(value: datetime, interval: str) -> datetime:
    days = {"daily": 1, "weekly": 7, "monthly": 30, "yearly": 365}.get(interval, 30)
    return value + timedelta(days=days)


def _forecast_groups(db: Session, filters: AnalyticsFilters, now: datetime) -> list[dict[str, Any]]:
    stmt = (
        select(
            Plan.id.label("plan_id"),
            Plan.name.label("plan_name"),
            Plan.interval.label("interval"),
            Plan.price.label("price"),
            Plan.product_id.label("product_id"),
            Plan.auto_close.label("auto_close"),
            Plan.available_until.label("available_until"),
            Subscription.quantity.label("quantity"),
            Subscription.next_billing_at.label("next_billing_at"),
            Subscription.current_period_end.label("current_period_end"),
            Subscription.cancel_at_period_end.label("cancel_at_period_end"),
            func.coalesce(ProductVariant.price_adjustment, 0).label("variant_adjustment"),
            func.count(Subscription.id).label("subscription_count"),
        )
        .select_from(Subscription)
        .join(Plan, Subscription.plan_id == Plan.id)
        .outerjoin(ProductVariant, Subscription.variant_id == ProductVariant.id)
        .where(Subscription.status == ACTIVE_STATUS, *_subscription_scope(filters))
        .group_by(
            Plan.id,
            Plan.name,
            Plan.interval,
            Plan.price,
            Plan.product_id,
            Plan.auto_close,
            Plan.available_until,
            Subscription.quantity,
            Subscription.next_billing_at,
            Subscription.current_period_end,
            Subscription.cancel_at_period_end,
            ProductVariant.price_adjustment,
        )
    )
    discounts = list(
        db.scalars(select(Discount).where(Discount.active.is_(True)).order_by(Discount.id))
    )
    groups = []
    for row in db.execute(stmt):
        quantity = int(row.quantity or 1)
        unit = _decimal(row.price) + _decimal(row.variant_adjustment)
        subtotal = (unit * quantity).quantize(MONEY, rounding=ROUND_HALF_UP)
        discount = _discount_for(quantity, subtotal, row.product_id, discounts, now)
        if discount:
            if _decimal(discount.percent_off) > 0:
                subtotal = (
                    subtotal * (Decimal("100") - _decimal(discount.percent_off)) / Decimal("100")
                ).quantize(MONEY, rounding=ROUND_HALF_UP)
            elif _decimal(discount.amount_off) > 0:
                subtotal = max(ZERO, subtotal - _decimal(discount.amount_off)).quantize(
                    MONEY, rounding=ROUND_HALF_UP
                )
        monthly = {
            "daily": subtotal * Decimal("365") / Decimal("12"),
            "weekly": subtotal * Decimal("52") / Decimal("12"),
            "monthly": subtotal,
            "yearly": subtotal / Decimal("12"),
        }.get(row.interval or "monthly", subtotal)
        groups.append(
            {
                "plan_id": int(row.plan_id),
                "plan_name": row.plan_name,
                "interval": row.interval or "monthly",
                "quantity": quantity,
                "count": int(row.subscription_count or 0),
                "subtotal": subtotal,
                "monthly": _money(monthly),
                "next_billing_at": _as_aware(row.next_billing_at),
                "current_period_end": _as_aware(row.current_period_end),
                "cancel_at_period_end": bool(row.cancel_at_period_end),
                "auto_close": bool(row.auto_close),
                "available_until": _as_aware(row.available_until),
            }
        )
    return groups


def forecast(db: Session, filters: AnalyticsFilters, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    groups = _forecast_groups(db, filters, now)
    month_cursor = _month_start(now.date())
    months = []
    for _ in range(6):
        start_dt, end_dt = _month_bounds(month_cursor)
        days_in_month = (end_dt.date() - start_dt.date()).days
        recurring = ZERO
        invoice_value = ZERO
        active_count = 0
        cancellations = 0
        closures = 0
        for group in groups:
            active_end = None
            if group["cancel_at_period_end"]:
                active_end = group["current_period_end"]
                if active_end and start_dt <= active_end < end_dt:
                    cancellations += group["count"]
            if group["auto_close"] and group["available_until"]:
                plan_end = group["available_until"]
                active_end = min(active_end, plan_end) if active_end else plan_end
                if start_dt <= plan_end < end_dt:
                    closures += group["count"]
            effective_start = max(start_dt, now)
            effective_end = min(end_dt, active_end) if active_end else end_dt
            if effective_end > effective_start:
                active_days = (effective_end - effective_start).total_seconds() / 86400
                recurring += group["monthly"] * group["count"] * Decimal(str(active_days / days_in_month))
                active_count += group["count"]
                cursor = group["next_billing_at"] or now
                if cursor < now:
                    cursor = now
                guard = 0
                while cursor < effective_end and guard < 400:
                    if cursor >= effective_start:
                        invoice_value += group["subtotal"] * group["count"]
                    cursor = _add_interval(cursor, group["interval"])
                    guard += 1
        months.append(
            {
                "month": month_cursor.isoformat(),
                "label": month_name[month_cursor.month][:3] + " " + str(month_cursor.year),
                "forecasted_recurring_revenue": _f(_money(recurring)),
                "expected_invoice_value": _f(_money(invoice_value)),
                "active_subscriptions": active_count,
                "scheduled_cancellations": cancellations,
                "plan_closures": closures,
            }
        )
        month_cursor = _next_month(month_cursor)

    scheduled_stmt = (
        select(Subscription, Plan, User)
        .select_from(Subscription)
        .join(Plan, Subscription.plan_id == Plan.id)
        .join(User, Subscription.customer_id == User.id)
        .where(
            Subscription.status == ACTIVE_STATUS,
            Subscription.cancel_at_period_end.is_(True),
            Subscription.current_period_end >= now,
            Subscription.current_period_end <= now + timedelta(days=183),
            *_event_scope(filters),
        )
        .order_by(Subscription.current_period_end)
        .limit(20)
    )
    scheduled = []
    for subscription, plan, customer in db.execute(scheduled_stmt):
        scheduled.append(
            {
                "subscription_id": subscription.id,
                "customer": customer.full_name,
                "plan": plan.name,
                "ends_at": _as_aware(subscription.current_period_end).isoformat(),
                "reason": subscription.cancellation_reason or "unspecified",
            }
        )

    closure_stmt = (
        select(Plan)
        .where(
            Plan.auto_close.is_(True),
            Plan.available_until.is_not(None),
            Plan.available_until >= now,
            Plan.available_until <= now + timedelta(days=183),
        )
        .order_by(Plan.available_until)
        .limit(20)
    )
    plan_closures = [
        {
            "plan_id": plan.id,
            "plan": plan.name,
            "closes_at": _as_aware(plan.available_until).isoformat(),
        }
        for plan in db.scalars(closure_stmt)
    ]

    renewals = upcoming_renewals(db, filters, days=183, limit=20, now=now)
    three = sum((_decimal(item["forecasted_recurring_revenue"]) for item in months[:3]), ZERO)
    six = sum((_decimal(item["forecasted_recurring_revenue"]) for item in months[:6]), ZERO)
    return {
        "months": months,
        "next_3_month_total": _f(_money(three)),
        "next_6_month_total": _f(_money(six)),
        "expected_recurring_revenue": months[0]["forecasted_recurring_revenue"] if months else 0,
        "expected_invoice_value": _f(
            sum((_decimal(item["expected_invoice_value"]) for item in months), ZERO)
        ),
        "upcoming_scheduled_cancellations": scheduled,
        "upcoming_plan_closures": plan_closures,
        "expected_renewals": renewals,
        "methodology": (
            "Deterministic schedule-based forecast from active subscriptions. It is not an "
            "AI prediction or a statistically trained model. Recurring revenue is prorated "
            "for partial months; expected invoice value excludes tax and uses the current "
            "recurring price and billing schedule."
        ),
        "currency": "INR",
    }


# ---------------------------------------------------------------------------
# Overview, snapshots and filter options
# ---------------------------------------------------------------------------


def _previous_period(filters: AnalyticsFilters) -> AnalyticsFilters:
    length = filters.end - filters.start
    return AnalyticsFilters(
        start=filters.start - length,
        end=filters.start,
        granularity=filters.granularity,
        plan_id=filters.plan_id,
        product_id=filters.product_id,
        customer_id=filters.customer_id,
    )


def _change(current: Decimal | int | float | None, previous: Decimal | int | float | None) -> dict[str, Any]:
    if current is None or previous is None:
        return {"previous": None, "percent_change": None, "direction": None}
    current_d = _decimal(current)
    previous_d = _decimal(previous)
    if previous_d == 0:
        return {
            "previous": _f(previous_d),
            "percent_change": None,
            "direction": "up" if current_d > 0 else ("down" if current_d < 0 else "flat"),
        }
    pct = (current_d - previous_d) / previous_d * 100
    return {
        "previous": _f(previous_d),
        "percent_change": _f(pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "direction": "up" if pct > 0 else ("down" if pct < 0 else "flat"),
    }


def _latest_snapshot(db: Session, month: date) -> AnalyticsSnapshot | None:
    return db.scalar(select(AnalyticsSnapshot).where(AnalyticsSnapshot.snapshot_month == _month_start(month)))


def overview(db: Session, filters: AnalyticsFilters, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    mrr = current_mrr(db, filters)
    active_rows = current_mrr_rows(db, filters)
    active_subscriptions = sum(row["subscription_count"] for row in active_rows)
    active_customers = int(
        db.scalar(
            select(func.count(func.distinct(Subscription.customer_id)))
            .select_from(Subscription)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(Subscription.status == ACTIVE_STATUS, *_event_scope(filters))
        )
        or 0
    )
    arpu = mrr / active_customers if active_customers else ZERO
    churn = churn_metrics(db, filters)
    outstanding = _money(
        db.scalar(
            select(func.coalesce(func.sum(Invoice.amount_due), 0))
            .select_from(Invoice)
            .join(Subscription, Invoice.subscription_id == Subscription.id)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(
                Invoice.status.not_in(["paid", "cancelled"]),
                *_event_scope(filters),
            )
        )
    )
    current_revenue = _collected_total(db, filters)
    previous_filters = _previous_period(filters)
    previous_revenue = _collected_total(db, previous_filters)

    latest_month = _month_start(filters.end_date)
    previous_month = _month_start(
        date(
            latest_month.year - (latest_month.month == 1),
            12 if latest_month.month == 1 else latest_month.month - 1,
            1,
        )
    )
    previous_month_end = datetime.combine(latest_month, time.min, tzinfo=timezone.utc)
    snap = _latest_snapshot(db, previous_month)
    if snap and isinstance(snap.metrics, dict) and snap.metrics.get("mrr") is not None:
        previous_mrr = _decimal(snap.metrics["mrr"])
        previous_mrr_source = "snapshot"
    else:
        previous_mrr = historical_mrr(db, filters, previous_month_end)
        previous_mrr_source = "reconstructed"
    previous_subs, previous_customers = historical_active_counts(db, filters, previous_month_end)
    previous_arpu = previous_mrr / previous_customers if previous_customers else ZERO

    reliability = _history_reliability(db)
    return {
        "mrr": _f(mrr),
        "arr": _f(_money(mrr * 12)),
        "active_subscriptions": active_subscriptions,
        "active_customers": active_customers,
        "arpu": _f(_money(arpu)),
        "churn_rate": churn["customer_churn_rate"],
        "subscription_churn_rate": churn["subscription_churn_rate"],
        "outstanding_revenue": _f(outstanding),
        "revenue_growth": {
            "current_period_revenue": _f(current_revenue),
            "previous_period_revenue": _f(previous_revenue),
            "percent_change": _change(current_revenue, previous_revenue)["percent_change"],
            "direction": _change(current_revenue, previous_revenue)["direction"],
            "basis": "Succeeded payments; cancelled invoices excluded.",
        },
        "changes": {
            "mrr": {**_change(mrr, previous_mrr), "source": previous_mrr_source},
            "arr": {**_change(mrr * 12, previous_mrr * 12), "source": previous_mrr_source},
            "active_subscriptions": _change(active_subscriptions, previous_subs),
            "active_customers": _change(active_customers, previous_customers),
            "arpu": _change(arpu, previous_arpu),
            "churn_rate": {
                "previous": churn["previous_customer_churn_rate"],
                "point_change": (
                    _f(Decimal(str(churn["customer_churn_rate"])) - Decimal(str(churn["previous_customer_churn_rate"])))
                    if churn["customer_churn_rate"] is not None and churn["previous_customer_churn_rate"] is not None
                    else None
                ),
            },
        },
        "renewal_rate": {
            "value": None,
            "available": False,
            "reason": (
                "The current schema does not record every renewal cycle. Only manual re-subscriptions "
                "are linked with renewed_from_id, so a reliable renewal rate cannot be calculated yet."
            ),
        },
        "reliability": reliability,
        "meta": {
            "currency": "INR",
            "generated_at": now.isoformat(),
            "filters": filters.scope,
            "mrr_basis": (
                "Active subscriptions only; quantity and variants included; eligible recurring "
                "discounts applied; tax excluded; daily/weekly/yearly normalised to a month."
            ),
            "previous_mrr_basis": (
                "Captured monthly snapshot." if previous_mrr_source == "snapshot" else
                "Reconstructed from subscription start/closure dates using current prices; "
                "the schema does not retain plan price history."
            ),
            "outstanding_basis": "Non-paid, non-cancelled invoices; gross amount including tax.",
        },
    }


def filter_options(db: Session) -> dict[str, Any]:
    plans = [
        {"id": plan.id, "name": plan.name, "interval": plan.interval}
        for plan in db.scalars(select(Plan).order_by(Plan.name))
    ]
    products = [
        {"id": product.id, "name": product.name}
        for product in db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.name))
    ]
    customers = [
        {"id": user.id, "name": user.full_name, "email": user.email}
        for user in db.scalars(
            select(User).where(User.role == "customer").order_by(User.full_name).limit(500)
        )
    ]
    return {
        "plans": plans,
        "products": products,
        "customers": customers,
        "cancellation_reasons": list(CANCELLATION_REASONS),
    }


def build_snapshot_metrics(db: Session, month: date, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    start, end = _month_bounds(_month_start(month))
    filters = AnalyticsFilters(start=start, end=end, granularity="month")
    ov = overview(db, filters, now=now)
    churn = churn_metrics(db, filters)
    revenue = revenue_series(db, filters)
    customers = segmentation(db, filters, now=now)
    return {
        "mrr": ov["mrr"],
        "arr": ov["arr"],
        "active_subscriptions": ov["active_subscriptions"],
        "active_customers": ov["active_customers"],
        "arpu": ov["arpu"],
        "outstanding_revenue": ov["outstanding_revenue"],
        "collected_revenue": revenue["total_collected"],
        "invoiced_revenue": revenue["total_invoiced"],
        "customer_churn_rate": churn["customer_churn_rate"],
        "subscription_churn_rate": churn["subscription_churn_rate"],
        "churned_customers": churn["customers_lost"],
        "churned_subscriptions": churn["churned_subscriptions"],
        "high_value_customers": customers["high_value_customers"],
        "segments": {item["key"]: item["count"] for item in customers["segments"]},
    }


def capture_snapshot(
    db: Session,
    month: date,
    *,
    overwrite: bool = False,
    now: datetime | None = None,
) -> tuple[AnalyticsSnapshot, bool]:
    """Create a monthly snapshot. Returns ``(snapshot, created)``.

    Existing rows are never overwritten unless ``overwrite`` is explicitly set.
    """
    month = _month_start(month)
    existing = _latest_snapshot(db, month)
    if existing and not overwrite:
        return existing, False
    metrics = build_snapshot_metrics(db, month, now=now)
    if existing:
        existing.metrics = metrics
        existing.source = "corrected"
        existing.captured_at = now or datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing, False
    snapshot = AnalyticsSnapshot(
        snapshot_month=month,
        captured_at=now or datetime.now(timezone.utc),
        source="captured" if month == _month_start((now or datetime.now(timezone.utc)).date()) else "reconstructed",
        metrics=metrics,
    )
    db.add(snapshot)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent runner may have inserted the same unique month first.
        db.rollback()
        existing = _latest_snapshot(db, month)
        if existing:
            return existing, False
        raise
    db.refresh(snapshot)
    return snapshot, True


def snapshot_series(db: Session, filters: AnalyticsFilters) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(AnalyticsSnapshot)
            .where(AnalyticsSnapshot.snapshot_month >= _month_start(filters.start_date))
            .where(AnalyticsSnapshot.snapshot_month < _month_start(filters.end_date) + timedelta(days=1))
            .order_by(AnalyticsSnapshot.snapshot_month)
        )
    )
    return [
        {
            "month": row.snapshot_month.isoformat(),
            "captured_at": _as_aware(row.captured_at).isoformat(),
            "source": row.source,
            "metrics": row.metrics,
        }
        for row in rows
    ]


def mrr_growth(db: Session, filters: AnalyticsFilters, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    snapshots = {item["month"]: item for item in snapshot_series(db, filters)}
    series = []
    for bucket in period_buckets(filters):
        # The movement columns describe the whole selected bucket, not only its
        # first calendar month. This matters for quarterly and yearly views.
        start = datetime.combine(
            date.fromisoformat(bucket["start"]), time.min, tzinfo=timezone.utc
        )
        end = datetime.combine(
            date.fromisoformat(bucket["end"]) + timedelta(days=1),
            time.min,
            tzinfo=timezone.utc,
        )
        end_month = _month_start((end - timedelta(microseconds=1)).date())
        snap = snapshots.get(end_month.isoformat())
        if snap and snap["metrics"].get("mrr") is not None:
            ending = _decimal(snap["metrics"]["mrr"])
            ending_source = "snapshot"
        else:
            ending = historical_mrr(db, filters, end)
            ending_source = "reconstructed"
        previous = historical_mrr(db, filters, start)
        new_rows = _periodic_totals(
            db,
            filters,
            [
                Subscription.started_at >= start,
                Subscription.started_at < end,
                *_subscription_scope(filters),
            ],
        )
        churned_rows = _periodic_totals(
            db,
            filters,
            [
                Subscription.closed_at.is_not(None),
                Subscription.closed_at >= start,
                Subscription.closed_at < end,
                *_subscription_scope(filters),
            ],
        )
        new_mrr = _money(_sum_rows(new_rows))
        churned = _money(_sum_rows(churned_rows))
        series.append(
            {
                **bucket,
                "previous_mrr": _f(previous),
                "new_mrr": _f(new_mrr),
                "expansion_mrr": 0,
                "contraction_mrr": 0,
                "churned_mrr": _f(churned),
                "ending_mrr": _f(ending),
                "source": ending_source,
                "reconstructed": ending_source == "reconstructed",
                "movement_note": (
                    "Expansion and contraction require a stored quantity/price change history. "
                    "The current application has no subscription edit flow, so both are reported as zero."
                ),
            }
        )
    return {
        "series": series,
        "snapshots": list(snapshots.values()),
        "snapshot_note": (
            "Snapshots are captured by the analytics snapshot runner. Months without a snapshot "
            "are reconstructed from subscription start/closure dates and current prices."
        ),
        "currency": "INR",
    }

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.dependencies import require_roles
from backend.app.models import User
from backend.app.services import analytics as service

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def analytics_filters(
    start_date: date | None = Query(default=None, description="Inclusive UTC start date"),
    end_date: date | None = Query(default=None, description="Inclusive UTC end date"),
    granularity: Literal["month", "quarter", "year"] = Query(default="month"),
    plan_id: int | None = Query(default=None, gt=0),
    product_id: int | None = Query(default=None, gt=0),
    customer_id: int | None = Query(default=None, gt=0),
) -> service.AnalyticsFilters:
    try:
        return service.default_filters(
            start_date=start_date,
            end_date=end_date,
            granularity=granularity,
            plan_id=plan_id,
            product_id=product_id,
            customer_id=customer_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/overview")
def overview(
    filters: service.AnalyticsFilters = Depends(analytics_filters),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "staff")),
):
    return service.overview(db, filters)


@router.get("/revenue")
def revenue(
    filters: service.AnalyticsFilters = Depends(analytics_filters),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "staff")),
):
    return {
        **service.revenue_series(db, filters),
        "breakdown": service.revenue_breakdown(db, filters),
        "mrr_growth": service.mrr_growth(db, filters),
        "filters": filters.scope,
    }


@router.get("/subscriptions")
def subscriptions(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    filters: service.AnalyticsFilters = Depends(analytics_filters),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "staff")),
):
    return {
        "status_distribution": service.status_distribution(db, filters),
        "growth": service.subscription_growth(db, filters),
        "churn": service.churn_metrics(db, filters),
        "upcoming_renewals": service.upcoming_renewals(
            db, filters, days=days, limit=limit, offset=offset
        ),
        "cancellation_reasons": list(service.CANCELLATION_REASONS),
        "filters": filters.scope,
    }


@router.get("/customers")
def customers(
    filters: service.AnalyticsFilters = Depends(analytics_filters),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "staff")),
):
    return service.customer_analytics(db, filters)


@router.get("/customer-segments/{segment}")
def customer_segment(
    segment: str,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    filters: service.AnalyticsFilters = Depends(analytics_filters),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "staff")),
):
    try:
        return service.customer_segment_rows(
            db, filters, segment, limit=limit, offset=offset
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/plans")
def plans(
    sort: Literal["revenue", "subscribers", "growth", "cancellations"] = Query(default="revenue"),
    filters: service.AnalyticsFilters = Depends(analytics_filters),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "staff")),
):
    return service.plan_performance(db, filters, sort=sort)


@router.get("/forecast")
def forecast(
    filters: service.AnalyticsFilters = Depends(analytics_filters),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "staff")),
):
    return service.forecast(db, filters)


@router.get("/options")
def options(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "staff")),
):
    return service.filter_options(db)

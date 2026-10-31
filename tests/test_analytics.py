from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base, get_db
from backend.app.main import app
from backend.app.models import (
    Discount,
    Invoice,
    Payment,
    Plan,
    Product,
    Subscription,
    User,
)
from backend.app.security import create_access_token, hash_password


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _add_months(value: date, months: int) -> date:
    index = value.month - 1 + months
    return date(value.year + index // 12, index % 12 + 1, 1)


@pytest.fixture
def analytics_env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    now = datetime.now(timezone.utc)
    today = now.date()
    current_month = _month_start(today)
    previous_month = _add_months(current_month, -1)
    two_months = _add_months(current_month, -2)
    three_months = _add_months(current_month, -3)
    four_months = _add_months(current_month, -4)

    with TestingSession() as db:
        admin = User(
            email="admin@analytics.test",
            full_name="Analytics Admin",
            password_hash=hash_password("StrongAdminPassword123!"),
            role="admin",
            created_at=four_months,
        )
        staff = User(
            email="staff@analytics.test",
            full_name="Analytics Staff",
            password_hash=hash_password("StrongStaffPassword123!"),
            role="staff",
            created_at=four_months,
        )
        alice = User(
            email="alice@analytics.test",
            full_name="Alice Active",
            password_hash=hash_password("StrongCustomerPassword123!"),
            role="customer",
            created_at=four_months,
        )
        bob = User(
            email="bob@analytics.test",
            full_name="Bob Active",
            password_hash=hash_password("StrongCustomerPassword123!"),
            role="customer",
            created_at=two_months,
        )
        carol = User(
            email="carol@analytics.test",
            full_name="Carol Churned",
            password_hash=hash_password("StrongCustomerPassword123!"),
            role="customer",
            created_at=previous_month,
        )
        cloud = Product(
            name="Cloud Suite", product_type="service", sales_price=Decimal("1000.00")
        )
        legacy = Product(
            name="Legacy Suite", product_type="service", sales_price=Decimal("500.00")
        )
        db.add_all([admin, staff, alice, bob, carol, cloud, legacy])
        db.flush()
        monthly = Plan(
            name="Monthly Pro",
            description="",
            price=Decimal("1000.00"),
            interval="monthly",
            product_id=cloud.id,
        )
        daily = Plan(
            name="Daily Ops",
            description="",
            price=Decimal("10.00"),
            interval="daily",
            product_id=cloud.id,
        )
        weekly = Plan(
            name="Weekly Ops",
            description="",
            price=Decimal("100.00"),
            interval="weekly",
            product_id=cloud.id,
        )
        yearly = Plan(
            name="Annual Pro",
            description="",
            price=Decimal("1200.00"),
            interval="yearly",
            product_id=cloud.id,
        )
        legacy_plan = Plan(
            name="Legacy",
            description="",
            price=Decimal("500.00"),
            interval="monthly",
            product_id=legacy.id,
        )
        db.add_all([monthly, daily, weekly, yearly, legacy_plan])
        db.flush()

        s1 = Subscription(
            customer_id=alice.id,
            plan_id=monthly.id,
            status="active",
            started_at=three_months,
            current_period_end=now + timedelta(days=30),
            next_billing_at=now + timedelta(days=5),
            number="SUB-A1",
            quantity=2,
        )
        s2 = Subscription(
            customer_id=bob.id,
            plan_id=daily.id,
            status="active",
            started_at=two_months,
            current_period_end=now + timedelta(days=1),
            next_billing_at=now + timedelta(days=20),
            number="SUB-B1",
            quantity=1,
        )
        s3 = Subscription(
            customer_id=bob.id,
            plan_id=weekly.id,
            status="active",
            started_at=two_months,
            current_period_end=now + timedelta(days=10),
            next_billing_at=now + timedelta(days=40),
            number="SUB-B2",
            quantity=1,
            cancel_at_period_end=True,
            cancellation_requested_at=now - timedelta(days=1),
            cancellation_reason="no_longer_needed",
        )
        s4 = Subscription(
            customer_id=alice.id,
            plan_id=yearly.id,
            status="active",
            started_at=now - timedelta(days=2),
            current_period_end=now + timedelta(days=60),
            next_billing_at=now + timedelta(days=60),
            number="SUB-A2",
            quantity=1,
        )
        s5 = Subscription(
            customer_id=bob.id,
            plan_id=monthly.id,
            status="paused",
            started_at=two_months,
            current_period_end=now + timedelta(days=20),
            next_billing_at=now + timedelta(days=20),
            number="SUB-B3",
            quantity=1,
        )
        s6 = Subscription(
            customer_id=carol.id,
            plan_id=monthly.id,
            status="closed",
            started_at=previous_month,
            current_period_end=now - timedelta(days=5),
            next_billing_at=now - timedelta(days=5),
            number="SUB-C1",
            quantity=1,
            cancel_at_period_end=False,
            closed_at=now - timedelta(days=5),
            cancellation_reason="too_expensive",
        )
        db.add_all([s1, s2, s3, s4, s5, s6])
        db.flush()

        paid_invoice = Invoice(
            subscription_id=s1.id,
            number="INV-A1",
            period_key="current-a1",
            subtotal=Decimal("2000.00"),
            tax_amount=Decimal("360.00"),
            amount_due=Decimal("2360.00"),
            status="paid",
            due_at=now,
            created_at=now - timedelta(days=3),
        )
        open_invoice = Invoice(
            subscription_id=s1.id,
            number="INV-A2",
            period_key="current-a2",
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("180.00"),
            amount_due=Decimal("1180.00"),
            status="open",
            due_at=now - timedelta(days=2),
            created_at=now - timedelta(days=2),
        )
        cancelled_invoice = Invoice(
            subscription_id=s2.id,
            number="INV-B1",
            period_key="current-b1",
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("90.00"),
            amount_due=Decimal("590.00"),
            status="cancelled",
            due_at=now,
            created_at=now - timedelta(days=1),
        )
        split_invoice = Invoice(
            subscription_id=s4.id,
            number="INV-A3",
            period_key="current-a3",
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("0.00"),
            amount_due=Decimal("1000.00"),
            status="paid",
            due_at=now,
            created_at=now - timedelta(days=1),
        )
        previous_invoice = Invoice(
            subscription_id=s1.id,
            number="INV-P1",
            period_key="previous-p1",
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("0.00"),
            amount_due=Decimal("500.00"),
            status="paid",
            due_at=previous_month,
            created_at=previous_month + timedelta(days=3),
        )
        db.add_all([paid_invoice, open_invoice, cancelled_invoice, split_invoice, previous_invoice])
        db.flush()
        db.add_all(
            [
                Payment(
                    invoice_id=paid_invoice.id,
                    amount=Decimal("2360.00"),
                    status="succeeded",
                    provider_reference="pay-a1",
                    method="upi",
                    created_at=now - timedelta(days=3),
                ),
                Payment(
                    invoice_id=split_invoice.id,
                    amount=Decimal("400.00"),
                    status="succeeded",
                    provider_reference="pay-a3-1",
                    method="card",
                    created_at=now - timedelta(days=1),
                ),
                Payment(
                    invoice_id=split_invoice.id,
                    amount=Decimal("600.00"),
                    status="succeeded",
                    provider_reference="pay-a3-2",
                    method="card",
                    created_at=now - timedelta(days=1),
                ),
                Payment(
                    invoice_id=previous_invoice.id,
                    amount=Decimal("500.00"),
                    status="succeeded",
                    provider_reference="pay-p1",
                    method="bank_transfer",
                    created_at=previous_month + timedelta(days=3),
                ),
            ]
        )
        db.commit()
        ids = {
            "admin": admin.id,
            "staff": staff.id,
            "alice": alice.id,
            "bob": bob.id,
            "carol": carol.id,
            "cloud": cloud.id,
            "legacy": legacy.id,
            "monthly": monthly.id,
            "daily": daily.id,
            "weekly": weekly.id,
            "yearly": yearly.id,
            "legacy_plan": legacy_plan.id,
            "s1": s1.id,
            "s2": s2.id,
            "s3": s3.id,
            "s4": s4.id,
            "s5": s5.id,
            "s6": s6.id,
            "open_invoice": open_invoice.id,
            "paid_invoice": paid_invoice.id,
            "cancelled_invoice": cancelled_invoice.id,
        }

    with TestClient(app) as test_client:
        tokens = {
            "admin": create_access_token(str(ids["admin"]), "admin"),
            "staff": create_access_token(str(ids["staff"]), "staff"),
            "alice": create_access_token(str(ids["alice"]), "customer"),
        }
        yield SimpleNamespace(
            client=test_client,
            db_factory=TestingSession,
            ids=ids,
            now=now,
            today=today,
            current_month=current_month,
            previous_month=previous_month,
            headers={
                "admin": {"Authorization": f"Bearer {tokens['admin']}"},
                "staff": {"Authorization": f"Bearer {tokens['staff']}"},
                "alice": {"Authorization": f"Bearer {tokens['alice']}"},
            },
            params={
                "start_date": current_month.isoformat(),
                "end_date": today.isoformat(),
            },
        )

    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _month_params(env):
    return dict(env.params)


def test_kpi_normalisation_quantity_arpu_and_outstanding(analytics_env):
    env = analytics_env
    response = env.client.get(
        "/api/analytics/overview", params=_month_params(env), headers=env.headers["admin"]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mrr"] == pytest.approx(2837.50, abs=0.01)
    assert body["arr"] == pytest.approx(34050.00, abs=0.01)
    assert body["active_subscriptions"] == 4
    assert body["active_customers"] == 2
    assert body["arpu"] == pytest.approx(1418.75, abs=0.01)
    assert body["outstanding_revenue"] == pytest.approx(1180.00, abs=0.01)
    assert body["renewal_rate"]["available"] is False
    assert "quantity" in body["meta"]["mrr_basis"]
    assert body["changes"]["mrr"]["source"] in {"snapshot", "reconstructed"}


def test_mrr_applies_eligible_recurring_discount(analytics_env):
    env = analytics_env
    with env.db_factory() as db:
        db.add(
            Discount(
                code="QTY10",
                percent_off=Decimal("10.00"),
                amount_off=Decimal("0.00"),
                minimum_quantity=2,
                minimum_amount=Decimal("0.00"),
                product_id=env.ids["cloud"],
                active=True,
            )
        )
        db.commit()
    response = env.client.get(
        "/api/analytics/overview", params=_month_params(env), headers=env.headers["admin"]
    )
    assert response.status_code == 200
    # Only the monthly subscription with quantity 2 qualifies for the 10% discount.
    assert response.json()["mrr"] == pytest.approx(2637.50, abs=0.01)


def test_revenue_is_payment_based_and_excludes_cancelled_invoices(analytics_env):
    env = analytics_env
    response = env.client.get(
        "/api/analytics/revenue", params=_month_params(env), headers=env.headers["admin"]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # 2360 + two payments against one invoice (400 + 600). The cancelled invoice is excluded.
    assert body["total_collected"] == pytest.approx(3360.00, abs=0.01)
    # Paid invoice 2360 + open invoice 1180 + split payment invoice 1000.
    assert body["total_invoiced"] == pytest.approx(4540.00, abs=0.01)
    assert body["series"]
    assert sum(item["collected"] for item in body["series"]) == pytest.approx(3360.00, abs=0.01)
    methods = {item["label"]: item["amount"] for item in body["breakdown"]["by_payment_method"]}
    assert methods["upi"] == pytest.approx(2360.00)
    assert methods["card"] == pytest.approx(1000.00)
    assert body["filters"]["start_date"] == env.current_month.isoformat()


def test_revenue_growth_compares_equal_previous_period(analytics_env):
    env = analytics_env
    # A fixed-length recent window keeps this test independent of the day the
    # suite runs: the fixture's current-month payments are at now-1 and now-3
    # days, while the older payment is roughly a month earlier.
    params = {
        "start_date": (env.today - timedelta(days=10)).isoformat(),
        "end_date": env.today.isoformat(),
    }
    response = env.client.get(
        "/api/analytics/overview", params=params, headers=env.headers["admin"]
    )
    growth = response.json()["revenue_growth"]
    assert growth["current_period_revenue"] == pytest.approx(3360.00, abs=0.01)
    assert growth["previous_period_revenue"] == pytest.approx(0.00, abs=0.01)
    assert growth["percent_change"] is None


def test_customer_growth_segments_clv_and_churn(analytics_env):
    env = analytics_env
    response = env.client.get(
        "/api/analytics/customers", params=_month_params(env), headers=env.headers["admin"]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_registered_customers"] == 3
    assert body["active_customers"] == 2
    assert body["customers_without_active_subscriptions"] == 1
    segment_counts = {item["key"]: item["count"] for item in body["segmentation"]["segments"]}
    assert segment_counts["active"] == 2
    assert segment_counts["inactive"] == 1
    assert segment_counts["overdue"] == 1
    assert segment_counts["ending_soon"] == 2  # Alice's monthly and Bob's weekly cancellation
    assert segment_counts["scheduled_cancellation"] == 1
    assert body["clv"]["available"] is True
    # ARPU 1418.75 / 33.33% churn ~= 4256.25
    assert body["clv"]["estimate"] == pytest.approx(4256.25, abs=0.5)


def test_subscription_status_growth_churn_reasons_and_renewals(analytics_env):
    env = analytics_env
    response = env.client.get(
        "/api/analytics/subscriptions",
        params={**_month_params(env), "days": 30, "limit": 100},
        headers=env.headers["staff"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    statuses = {item["status"]: item["count"] for item in body["status_distribution"]}
    assert statuses == {"active": 4, "closed": 1, "paused": 1}
    growth = body["growth"][-1]
    assert growth["new_subscriptions"] == 1
    assert growth["cancelled_subscriptions"] == 1
    assert growth["closed_subscriptions"] == 0
    assert body["churn"]["customer_churn_rate"] == pytest.approx(33.33, abs=0.01)
    assert body["churn"]["churned_subscriptions"] == 1
    assert body["churn"]["churned_mrr"] == pytest.approx(1000.00, abs=0.01)
    assert body["churn"]["reasons"][0]["reason"] == "too_expensive"
    assert body["upcoming_renewals"]["count"] == 2
    assert {item["subscription_id"] for item in body["upcoming_renewals"]["items"]} == {
        env.ids["s1"],
        env.ids["s2"],
    }
    # 7-day window only contains the monthly renewal.
    seven = env.client.get(
        "/api/analytics/subscriptions",
        params={**_month_params(env), "days": 7, "limit": 100},
        headers=env.headers["staff"],
    ).json()
    assert seven["upcoming_renewals"]["count"] == 1


def test_scheduled_cancellation_is_not_counted_as_closed(analytics_env):
    env = analytics_env
    with env.db_factory() as db:
        sub = db.get(Subscription, env.ids["s3"])
        assert sub.cancel_at_period_end is True
        assert sub.closed_at is None
        assert sub.status == "active"
    response = env.client.get(
        "/api/analytics/subscriptions",
        params=_month_params(env),
        headers=env.headers["admin"],
    )
    body = response.json()
    assert body["churn"]["churned_subscriptions"] == 1
    assert body["status_distribution"][0]["status"] == "active"


def test_customer_can_inspect_segment_but_customer_role_is_denied(analytics_env):
    env = analytics_env
    denied = env.client.get(
        "/api/analytics/overview", params=_month_params(env), headers=env.headers["alice"]
    )
    assert denied.status_code == 403
    segment = env.client.get(
        "/api/analytics/customer-segments/overdue",
        params=_month_params(env),
        headers=env.headers["staff"],
    )
    assert segment.status_code == 200, segment.text
    assert segment.json()["count"] == 1
    assert segment.json()["items"][0]["email"] == "alice@analytics.test"


def test_plan_performance_and_revenue_contribution(analytics_env):
    env = analytics_env
    response = env.client.get(
        "/api/analytics/plans",
        params={**_month_params(env), "sort": "revenue"},
        headers=env.headers["admin"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    plans = {item["plan"]: item for item in body["plans"]}
    assert plans["Monthly Pro"]["mrr"] == pytest.approx(2000.00)
    assert plans["Monthly Pro"]["active_subscribers"] == 1
    assert plans["Monthly Pro"]["total_subscribers"] == 3  # active, paused, closed history
    assert plans["Daily Ops"]["mrr"] == pytest.approx(304.17, abs=0.01)
    assert plans["Weekly Ops"]["mrr"] == pytest.approx(433.33, abs=0.01)
    assert plans["Annual Pro"]["mrr"] == pytest.approx(100.00)
    assert plans["Annual Pro"]["arr"] == pytest.approx(1200.00)
    assert sum(item["revenue_contribution_percent"] for item in body["plans"]) == pytest.approx(
        100.00, abs=0.05
    )
    assert body["sort"] == "revenue"


def test_forecast_uses_active_schedules_and_scheduled_cancellation(analytics_env):
    env = analytics_env
    response = env.client.get(
        "/api/analytics/forecast", params=_month_params(env), headers=env.headers["admin"]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["months"]) == 6
    assert body["next_3_month_total"] > 0
    assert body["next_6_month_total"] >= body["next_3_month_total"]
    assert "not an ai prediction" in body["methodology"].lower()
    assert body["upcoming_scheduled_cancellations"][0]["subscription_id"] == env.ids["s3"]
    assert body["expected_renewals"]["count"] >= 1


def test_analytics_forecast_uses_calendar_billing_intervals():
    from backend.app.services.analytics import add_interval

    jan_31 = datetime(2024, 1, 31, 9, 30, tzinfo=timezone.utc)
    assert add_interval(jan_31, "monthly") == datetime(2024, 2, 29, 9, 30, tzinfo=timezone.utc)
    assert add_interval(jan_31, "yearly") == datetime(2025, 1, 31, 9, 30, tzinfo=timezone.utc)


def test_renew_linkage_populates_renewed_growth(analytics_env):
    env = analytics_env
    now = env.now
    with env.db_factory() as db:
        old = Subscription(
            customer_id=env.ids["alice"],
            plan_id=env.ids["legacy_plan"],
            status="closed",
            started_at=now - timedelta(days=90),
            current_period_end=now - timedelta(days=20),
            next_billing_at=now - timedelta(days=20),
            number="SUB-OLD-RENEW",
            quantity=1,
            closed_at=now - timedelta(days=20),
            cancellation_reason="other",
        )
        db.add(old)
        db.commit()
        old_id = old.id
    renewed = env.client.post(
        f"/api/subscriptions/{old_id}/renew", headers=env.headers["admin"]
    )
    assert renewed.status_code == 201, renewed.text
    with env.db_factory() as db:
        assert db.get(Subscription, renewed.json()["id"]).renewed_from_id == old_id
    body = env.client.get(
        "/api/analytics/subscriptions",
        params=_month_params(env),
        headers=env.headers["admin"],
    ).json()
    assert body["growth"][-1]["renewed_subscriptions"] == 1


def test_snapshot_generation_is_idempotent_and_does_not_overwrite(analytics_env):
    env = analytics_env
    from backend.app.services.analytics import capture_snapshot

    with env.db_factory() as db:
        first, created = capture_snapshot(db, env.current_month, now=env.now)
        assert created is True
        original_metrics = dict(first.metrics)
        second, created_again = capture_snapshot(db, env.current_month, now=env.now)
        assert created_again is False
        assert second.id == first.id
        assert second.metrics == original_metrics
        corrected, created_corrected = capture_snapshot(
            db, env.current_month, overwrite=True, now=env.now
        )
        assert created_corrected is False
        assert corrected.source == "corrected"


def test_filters_validate_and_apply_product_and_customer(analytics_env):
    env = analytics_env
    invalid = env.client.get(
        "/api/analytics/overview",
        params={"start_date": env.today.isoformat(), "end_date": env.current_month.isoformat()},
        headers=env.headers["admin"],
    )
    assert invalid.status_code == 422
    customer = env.client.get(
        "/api/analytics/overview",
        params={**_month_params(env), "customer_id": env.ids["bob"]},
        headers=env.headers["admin"],
    )
    assert customer.status_code == 200
    assert customer.json()["active_customers"] == 1
    assert customer.json()["mrr"] == pytest.approx(737.50, abs=0.01)
    product = env.client.get(
        "/api/analytics/plans",
        params={**_month_params(env), "product_id": env.ids["legacy"]},
        headers=env.headers["admin"],
    )
    assert product.status_code == 200
    assert [item["plan"] for item in product.json()["plans"]] == ["Legacy"]


def test_analytics_endpoints_require_authentication(analytics_env):
    env = analytics_env
    for path in [
        "/api/analytics/overview",
        "/api/analytics/revenue",
        "/api/analytics/subscriptions",
        "/api/analytics/customers",
        "/api/analytics/plans",
        "/api/analytics/forecast",
        "/api/analytics/options",
    ]:
        assert env.client.get(path).status_code == 401


def test_quarterly_and_yearly_aggregation_covers_full_bucket(analytics_env):
    env = analytics_env
    quarter_start = date(env.current_month.year, ((env.current_month.month - 1) // 3) * 3 + 1, 1)
    quarter_end = _add_months(quarter_start, 3) - timedelta(days=1)
    response = env.client.get(
        "/api/analytics/revenue",
        params={**_month_params(env), "granularity": "quarter"},
        headers=env.headers["admin"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["series"]) == 1
    assert body["series"][0]["start"] == quarter_start.isoformat()
    assert body["series"][0]["end"] == quarter_end.isoformat()
    assert sum(item["collected"] for item in body["series"]) == pytest.approx(3360.00)
    movement = body["mrr_growth"]["series"]
    assert len(movement) == 1
    assert movement[0]["start"] == quarter_start.isoformat()
    assert movement[0]["end"] == quarter_end.isoformat()

    yearly = env.client.get(
        "/api/analytics/revenue",
        params={**_month_params(env), "granularity": "year"},
        headers=env.headers["admin"],
    ).json()
    assert len(yearly["series"]) == 1
    assert yearly["series"][0]["start"] == f"{env.current_month.year}-01-01"
    assert yearly["series"][0]["end"] == f"{env.current_month.year}-12-31"


def test_forecast_counts_a_scheduled_cancellation_only_in_its_month(analytics_env):
    env = analytics_env
    end = env.now + timedelta(days=75)
    with env.db_factory() as db:
        db.add(
            Subscription(
                customer_id=env.ids["alice"],
                plan_id=env.ids["monthly"],
                status="active",
                started_at=env.now - timedelta(days=20),
                current_period_end=end,
                next_billing_at=end,
                number="SUB-FUTURE-CANCEL",
                quantity=1,
                cancel_at_period_end=True,
                cancellation_reason="switching_provider",
            )
        )
        db.commit()
    body = env.client.get(
        "/api/analytics/forecast", params=_month_params(env), headers=env.headers["admin"]
    ).json()
    # s3 (ends within days) plus the new cancellation (ends in ~75 days). Each
    # should appear exactly once, not in every subsequent forecast month.
    assert sum(item["scheduled_cancellations"] for item in body["months"]) == 2
    target = _month_start(end.date()).isoformat()
    assert next(item for item in body["months"] if item["month"] == target)["scheduled_cancellations"] >= 1


def test_cancel_reactivate_and_close_record_reason_and_timestamp(analytics_env):
    env = analytics_env
    subscription_id = env.ids["s2"]
    cancelled = env.client.post(
        f"/api/subscriptions/{subscription_id}/cancel",
        json={"reason": "no_longer_needed"},
        headers=env.headers["admin"],
    )
    assert cancelled.status_code == 200, cancelled.text
    with env.db_factory() as db:
        sub = db.get(Subscription, subscription_id)
        assert sub.cancel_at_period_end is True
        assert sub.cancellation_requested_at is not None
        assert sub.cancellation_reason == "no_longer_needed"
    reactivated = env.client.post(
        f"/api/subscriptions/{subscription_id}/reactivate", headers=env.headers["admin"]
    )
    assert reactivated.status_code == 200, reactivated.text
    with env.db_factory() as db:
        sub = db.get(Subscription, subscription_id)
        assert sub.cancellation_requested_at is None
        assert sub.cancellation_reason is None
    closed = env.client.post(
        f"/api/subscriptions/{subscription_id}/close",
        json={"reason": "business_closed"},
        headers=env.headers["admin"],
    )
    assert closed.status_code == 200, closed.text
    with env.db_factory() as db:
        sub = db.get(Subscription, subscription_id)
        assert sub.status == "closed"
        assert sub.closed_at is not None
        assert sub.cancellation_reason == "business_closed"
    analytics = env.client.get(
        "/api/analytics/subscriptions", params=_month_params(env), headers=env.headers["admin"]
    ).json()
    reasons = {item["reason"]: item["count"] for item in analytics["churn"]["reasons"]}
    assert reasons.get("business_closed", 0) == 1


def test_empty_dataset_returns_zeros_without_division_errors():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestingSession() as db:
        admin = User(
            email="empty-admin@analytics.test",
            full_name="Empty Admin",
            password_hash=hash_password("StrongAdminPassword123!"),
            role="admin",
        )
        db.add(admin)
        db.commit()
        admin_id = admin.id
    headers = {"Authorization": f"Bearer {create_access_token(str(admin_id), 'admin')}"}
    try:
        with TestClient(app) as client:
            overview = client.get("/api/analytics/overview", headers=headers)
            assert overview.status_code == 200, overview.text
            body = overview.json()
            assert body["mrr"] == 0
            assert body["arr"] == 0
            assert body["arpu"] == 0
            assert body["churn_rate"] is None
            assert body["outstanding_revenue"] == 0
            revenue = client.get("/api/analytics/revenue", headers=headers).json()
            assert revenue["total_collected"] == 0
            assert revenue["total_invoiced"] == 0
            assert revenue["breakdown"]["by_plan"] == []
            customers = client.get("/api/analytics/customers", headers=headers).json()
            assert customers["active_customers"] == 0
            assert customers["clv"]["available"] is False
            plans = client.get("/api/analytics/plans", headers=headers).json()
            assert plans["plans"] == []
            forecast = client.get("/api/analytics/forecast", headers=headers).json()
            assert len(forecast["months"]) == 6
            assert forecast["next_6_month_total"] == 0
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


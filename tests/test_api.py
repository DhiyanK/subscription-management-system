from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend.app.database import Base, get_db
from backend.app.main import app
from backend.app.models import Plan, User
from backend.app.security import hash_password

@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
        db.add(User(email="admin@example.com", full_name="Admin", password_hash=hash_password("StrongAdminPassword123!"), role="admin"))
        db.add(Plan(name="Starter", description="", price=Decimal("499.00"), currency="INR", interval="monthly"))
        db.commit()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()

def test_health_and_dashboard_shell(client):
    assert client.get("/health").json()["status"] == "ok"
    assert "Brightloop" in client.get("/").text
    assert client.get("/static/css/app.css").status_code == 200

def test_registration_login_subscription_billing_flow(client):
    registered = client.post("/api/auth/register", json={"email":"customer@example.com", "full_name":"Customer", "password":"StrongCustomerPassword123!"})
    assert registered.status_code == 201
    token = client.post("/api/auth/login", json={"email":"customer@example.com", "password":"StrongCustomerPassword123!"}).json()["access_token"]
    customer_headers = {"Authorization": f"Bearer {token}"}
    customer_subscription = client.post("/api/subscriptions", headers=customer_headers, json={"plan_id":1})
    assert customer_subscription.status_code == 201
    assert client.get("/api/subscriptions", headers=customer_headers).json()[0]["id"] == customer_subscription.json()["id"]
    invoice = customer_subscription.json()["invoice"]
    assert invoice["status"] == "open"
    assert client.get("/api/invoices", headers=customer_headers).json()[0]["id"] == invoice["id"]
    admin_token = client.post("/api/auth/login", json={"email":"admin@example.com", "password":"StrongAdminPassword123!"}).json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    paid = client.post(f"/api/invoices/{invoice['id']}/payments", headers=admin_headers, json={"provider_reference":"reference-1"})
    assert paid.status_code == 201
    assert client.get("/api/invoices", headers=customer_headers).json()[0]["status"] == "paid"
    duplicate = client.post(f"/api/invoices/{invoice['id']}/payments", headers=admin_headers, json={"provider_reference":"reference-1"})
    assert duplicate.status_code == 409

def test_customer_selects_a_single_tax_rate_and_prints_a_complete_invoice(client):
    admin_token = client.post("/api/auth/login", json={"email":"admin@example.com", "password":"StrongAdminPassword123!"}).json()["access_token"]
    admin = {"Authorization": f"Bearer {admin_token}"}
    gst = client.post("/api/taxes", headers=admin, json={"name":"GST 18", "rate_percent":"18"}).json()
    other = client.post("/api/taxes", headers=admin, json={"name":"Other 5", "rate_percent":"5"}).json()
    customer = client.post("/api/auth/register", json={"email":"tax-choice@example.com", "full_name":"Tax Customer", "password":"StrongCustomerPassword123!"}).json()
    token = client.post("/api/auth/login", json={"email":"tax-choice@example.com", "password":"StrongCustomerPassword123!"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert len(client.get("/api/tax-options", headers=headers).json()) == 2
    created = client.post("/api/subscriptions", headers=headers, json={"plan_id":1, "tax_rule_id":gst["id"]})
    assert created.status_code == 201
    assert created.json()["invoice"]["amount_due"] == "588.82"
    invoice_id = created.json()["invoice"]["id"]
    invoice = client.get("/api/invoices", headers=headers).json()[0]
    assert invoice["lines"][0]["tax_rate"] == "18.00"

    printed = client.get(f"/api/invoices/{invoice_id}/print", headers=headers)
    assert printed.status_code == 200
    assert "Invoice INV-" in printed.text
    assert "Bill to" in printed.text
    assert "Subtotal" in printed.text and "Tax" in printed.text and "Invoice total" in printed.text
    assert "Print invoice" in printed.text

    manual = client.post("/api/invoices", headers=admin, json={"subscription_id":created.json()["id"], "tax_rule_id":other["id"]})
    assert manual.status_code == 201
    assert manual.json()["amount_due"] == "523.95"

def test_razorpay_checkout_verifies_capture_before_paying_invoice(client, monkeypatch):
    import hashlib
    import hmac
    from backend.app.core import settings
    from backend.app.routers import billing

    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_public")
    monkeypatch.setattr(settings, "razorpay_key_secret", "test_secret")
    customer = client.post("/api/auth/register", json={"email":"checkout@example.com", "full_name":"Checkout Customer", "password":"StrongCustomerPassword123!"}).json()
    token = client.post("/api/auth/login", json={"email":"checkout@example.com", "password":"StrongCustomerPassword123!"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    subscription = client.post("/api/subscriptions", headers=headers, json={"plan_id":1}).json()
    invoice = subscription["invoice"]

    def fake_razorpay_request(method, path, payload=None):
        if method == "POST" and path == "/orders":
            assert payload["amount"] == 49900
            return {"id":"order_test_123", "amount":payload["amount"], "currency":"INR"}
        if method == "GET" and path == "/payments/pay_test_123":
            return {"id":"pay_test_123", "order_id":"order_test_123", "amount":49900, "currency":"INR", "status":"captured"}
        raise AssertionError(f"Unexpected Razorpay request: {method} {path}")

    monkeypatch.setattr(billing, "_razorpay_request", fake_razorpay_request)
    order = client.post(f"/api/invoices/{invoice['id']}/razorpay-order", headers=headers)
    assert order.status_code == 201
    assert order.json()["key_id"] == "rzp_test_public"
    assert "test_secret" not in order.text
    signature = hmac.new(b"test_secret", b"order_test_123|pay_test_123", hashlib.sha256).hexdigest()
    verified = client.post("/api/invoices/razorpay/verify", headers=headers, json={
        "razorpay_order_id":"order_test_123",
        "razorpay_payment_id":"pay_test_123",
        "razorpay_signature":signature,
    })
    assert verified.status_code == 200
    assert verified.json()["status"] == "paid"
    assert client.get("/api/invoices", headers=headers).json()[0]["status"] == "paid"

def test_razorpay_captured_webhook_is_signature_checked_and_idempotent(client, monkeypatch):
    import hashlib
    import hmac
    import json
    from backend.app.core import settings
    from backend.app.routers import billing

    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_public")
    monkeypatch.setattr(settings, "razorpay_key_secret", "test_secret")
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "webhook_secret")
    customer = client.post("/api/auth/register", json={"email":"webhook@example.com", "full_name":"Webhook Customer", "password":"StrongCustomerPassword123!"}).json()
    token = client.post("/api/auth/login", json={"email":"webhook@example.com", "password":"StrongCustomerPassword123!"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    subscription = client.post("/api/subscriptions", headers=headers, json={"plan_id":1}).json()
    invoice = subscription["invoice"]
    monkeypatch.setattr(billing, "_razorpay_request", lambda method, path, payload=None: {"id":"order_hook_123"})
    assert client.post(f"/api/invoices/{invoice['id']}/razorpay-order", headers=headers).status_code == 201

    event = {"event":"payment.captured", "payload":{"payment":{"entity":{"id":"pay_hook_123", "order_id":"order_hook_123", "amount":49900, "currency":"INR", "status":"captured"}}}}
    raw = json.dumps(event, separators=(",", ":")).encode()
    signature = hmac.new(b"webhook_secret", raw, hashlib.sha256).hexdigest()
    webhook_headers = {"Content-Type":"application/json", "X-Razorpay-Signature":signature}
    assert client.post("/api/webhooks/razorpay", content=raw, headers=webhook_headers).status_code == 200
    assert client.post("/api/webhooks/razorpay", content=raw, headers=webhook_headers).status_code == 200
    assert client.get("/api/invoices", headers=headers).json()[0]["status"] == "paid"

    tampered = raw.replace(b"49900", b"49901")
    assert client.post("/api/webhooks/razorpay", content=tampered, headers=webhook_headers).status_code == 400

def test_role_access_is_enforced(client):
    response = client.post("/api/plans", json={"name":"Premium", "price":"20.00", "currency":"INR", "interval":"monthly"})
    assert response.status_code == 401

def test_recurring_billing_is_idempotent():
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from backend.app.models import Invoice, Subscription
    from backend.app.services.recurring_billing import generate_due_invoices
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        customer = User(email="billing@example.com", full_name="Billing", password_hash="not-used")
        plan = Plan(name="Recurring", description="", price=Decimal("100.00"), currency="INR", interval="monthly")
        db.add_all([customer, plan]); db.flush()
        now = datetime.now(timezone.utc)
        subscription = Subscription(customer_id=customer.id, plan_id=plan.id, status="active", started_at=now, next_billing_at=now - timedelta(minutes=1), current_period_end=now + timedelta(days=30))
        db.add(subscription); db.commit()
        assert generate_due_invoices(db, now) == 1
        assert generate_due_invoices(db, now) == 0
        assert len(list(db.scalars(select(Invoice)))) == 1
    engine.dispose()

def test_catalog_discounts_and_quotation_acceptance(client):
    admin_token = client.post("/api/auth/login", json={"email":"admin@example.com", "password":"StrongAdminPassword123!"}).json()["access_token"]
    admin = {"Authorization": f"Bearer {admin_token}"}
    product = client.post("/api/products", headers=admin, json={"name":"Cloud Suite", "product_type":"service", "sales_price":"1200.00", "cost_price":"300.00", "notes":""})
    assert product.status_code == 201
    variant = client.post(f"/api/products/{product.json()['id']}/variants", headers=admin, json={"name":"Odoo", "price_adjustment":"560.00"})
    assert variant.status_code == 201
    assert client.post("/api/discounts", headers=admin, json={"code":"TEAM10", "percent_off":"10", "product_id":product.json()["id"]}).status_code == 201
    customer = client.post("/api/auth/register", json={"email":"quote@example.com", "full_name":"Quote Customer", "password":"StrongCustomerPassword123!"}).json()
    template = client.post("/api/quotation-templates", headers=admin, json={"name":"Cloud annual offer", "plan_id":1, "validity_days":14})
    assert template.status_code == 201
    quote = client.post("/api/quotations", headers=admin, json={"customer_id":customer["id"], "template_id":template.json()["id"]})
    assert quote.status_code == 201
    customer_token = client.post("/api/auth/login", json={"email":"quote@example.com", "password":"StrongCustomerPassword123!"}).json()["access_token"]
    accepted = client.post(f"/api/quotations/{quote.json()['id']}/accept", headers={"Authorization":f"Bearer {customer_token}"})
    assert accepted.status_code == 200
    assert accepted.json()["subscription_status"] == "confirmed"
    activated = client.post(f"/api/quotations/subscriptions/{accepted.json()['subscription_id']}/activate", headers=admin)
    assert activated.status_code == 200
    assert activated.json()["invoice"]["status"] == "open"

def test_password_policy_rejects_weak_password(client):
    response = client.post("/api/auth/register", json={"email":"weak@example.com", "full_name":"Weak", "password":"alllowercase"})
    assert response.status_code == 422

def test_customer_cannot_access_admin_operations_even_with_admin_claim(client):
    from backend.app.models import User
    from backend.app.security import create_access_token
    customer = client.post("/api/auth/register", json={"email":"locked@example.com", "full_name":"Locked Customer", "password":"StrongCustomerPassword123!"}).json()
    # Deliberately forge the JWT role claim; authorization must use the persisted account role.
    forged_token = create_access_token(str(customer["id"]), "admin")
    headers = {"Authorization": f"Bearer {forged_token}"}
    assert client.get("/api/dashboard/summary", headers=headers).status_code == 403
    assert client.get("/api/customers", headers=headers).status_code == 403
    assert client.post("/api/auth/staff", headers=headers, json={"email":"newstaff@example.com", "full_name":"New Staff", "password":"StrongStaffPassword123!"}).status_code == 403
    assert client.post("/api/products", headers=headers, json={"name":"Restricted", "sales_price":"10", "cost_price":"0"}).status_code == 403
    assert client.post("/api/plans", headers=headers, json={"name":"Restricted Plan", "price":"10", "interval":"monthly"}).status_code == 403
    assert client.post("/api/discounts", headers=headers, json={"code":"RESTRICTED", "percent_off":"10"}).status_code == 403

def test_customer_reads_are_limited_to_self_service_data(client):
    customer = client.post("/api/auth/register", json={"email":"selfservice@example.com", "full_name":"Self Service", "password":"StrongCustomerPassword123!"}).json()
    token = client.post("/api/auth/login", json={"email":"selfservice@example.com", "password":"StrongCustomerPassword123!"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    # Plans are public so a customer can choose an offer; internal catalog and tax administration is not.
    assert client.get("/api/plans").status_code == 200
    assert client.get("/api/products", headers=headers).status_code == 403
    assert client.get("/api/taxes", headers=headers).status_code == 403
    assert client.get("/api/discounts", headers=headers).status_code == 403
    assert client.get("/api/dashboard/summary", headers=headers).status_code == 403
    assert client.get("/api/customers", headers=headers).status_code == 403
    assert client.get("/api/subscriptions", headers=headers).json() == []
    assert client.get("/api/invoices", headers=headers).json() == []

def test_admin_can_create_and_list_staff_but_customers_cannot(client):
    admin = client.post("/api/auth/login", json={"email":"admin@example.com", "password":"StrongAdminPassword123!"}).json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin}"}
    created = client.post("/api/auth/staff", headers=admin_headers, json={"email":"teammate@example.com", "full_name":"Team Mate", "password":"StrongStaffPassword123!"})
    assert created.status_code == 201
    listing = client.get("/api/customers/staff", headers=admin_headers)
    assert listing.status_code == 200
    assert listing.json()[0]["email"] == "teammate@example.com"
    customer = client.post("/api/auth/register", json={"email":"staff-list-customer@example.com", "full_name":"Customer", "password":"StrongCustomerPassword123!"}).json()
    token = client.post("/api/auth/login", json={"email":"staff-list-customer@example.com", "password":"StrongCustomerPassword123!"}).json()["access_token"]
    assert client.get("/api/customers/staff", headers={"Authorization":f"Bearer {token}"}).status_code == 403

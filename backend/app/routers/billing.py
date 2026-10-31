from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from email.message import EmailMessage
from html import escape
import base64
import hashlib
import hmac
import json
import smtplib
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from fastapi import APIRouter, Depends, HTTPException, Request as FastAPIRequest
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.core import settings
from backend.app.database import get_db
from backend.app.dependencies import current_user, require_roles
from backend.app.models import Discount, Invoice, InvoiceLine, Payment, Subscription, TaxRule, User

router = APIRouter(prefix="/api", tags=["billing"])
class InvoiceCreate(BaseModel):
    subscription_id: int
    discount_code: str | None = Field(default=None, max_length=40)
    tax_rule_id: int | None = None
class PaymentRecord(BaseModel):
    provider_reference: str = Field(min_length=1, max_length=120)
    method: str = Field(default="manual", pattern="^(cash|bank_transfer|card|upi|other|manual)$")
    note: str = Field(default="", max_length=500)
class RazorpayVerify(BaseModel):
    razorpay_order_id: str = Field(min_length=1, max_length=80)
    razorpay_payment_id: str = Field(min_length=1, max_length=80)
    razorpay_signature: str = Field(min_length=1, max_length=128)

def _tax_rate(db: Session, tax_rule_id: int | None) -> Decimal:
    if tax_rule_id is None:
        return Decimal("0")
    tax = db.get(TaxRule, tax_rule_id)
    if not tax or not tax.is_active:
        raise HTTPException(status_code=422, detail="Select an active tax rate")
    return tax.rate_percent

def _razorpay_configured():
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Razorpay is not configured. Add the API keys to the server environment.")

def _razorpay_request(method: str, path: str, payload: dict | None = None) -> dict:
    _razorpay_configured()
    credentials = base64.b64encode(f"{settings.razorpay_key_id}:{settings.razorpay_key_secret}".encode()).decode()
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(f"https://api.razorpay.com/v1{path}", data=data, method=method)
    request.add_header("Authorization", f"Basic {credentials}")
    request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=12) as response:
            return json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        # Provider response bodies may contain private account details; keep API errors generic.
        raise HTTPException(status_code=502, detail="Could not confirm the Razorpay payment. Please retry or contact support.") from error

def _amount_in_paise(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

@router.get("/invoices")
def list_invoices(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(Invoice).join(Subscription, Invoice.subscription_id == Subscription.id).order_by(Invoice.created_at.desc())
    if user.role == "customer":
        query = query.where(Subscription.customer_id == user.id)
    return [{"id": i.id, "number": i.number, "subscription_id": i.subscription_id, "amount_due": str(i.amount_due), "currency": i.currency, "status": i.status, "due_at": i.due_at, "created_at": i.created_at, "lines": [{"description": line.description, "quantity": line.quantity, "unit_price": str(line.unit_price), "tax_rate": str(line.tax_rate), "line_total": str(line.line_total)} for line in i.lines]} for i in db.scalars(query)]

@router.post("/invoices", status_code=201)
def create_invoice(data: InvoiceCreate, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "staff"))):
    subscription = db.get(Subscription, data.subscription_id)
    if not subscription or subscription.status != "active":
        raise HTTPException(status_code=404, detail="Active subscription not found")
    plan = subscription.plan
    amount = (plan.price + (subscription.variant.price_adjustment if subscription.variant else Decimal("0"))) * subscription.quantity
    if data.discount_code:
        discount = db.scalar(select(Discount).where(Discount.code == data.discount_code.upper(), Discount.active.is_(True)))
        if not discount:
            raise HTTPException(status_code=422, detail="Discount code is invalid or inactive")
        if discount.usage_limit and discount.usage_count >= discount.usage_limit:
            raise HTTPException(status_code=422, detail="Discount usage limit reached")
        if discount.percent_off:
            amount = (amount * (Decimal("100") - discount.percent_off) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            amount = max(Decimal("0"), amount - discount.amount_off)
        discount.usage_count += 1
    tax_rate = _tax_rate(db, data.tax_rule_id)
    subtotal = amount
    tax_amount = (subtotal * Decimal(str(tax_rate)) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amount = subtotal + tax_amount
    now = datetime.now(timezone.utc)
    invoice = Invoice(subscription_id=subscription.id, number=f"INV-{now:%Y%m%d%H%M%S}-{subscription.id}", period_key=f"manual-{now:%Y%m%d%H%M%S%f}", subtotal=subtotal, tax_amount=tax_amount, amount_due=amount, currency=plan.currency, status="open", due_at=now)
    db.add(invoice)
    db.flush()
    unit_price = (subtotal / subscription.quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    db.add(InvoiceLine(invoice_id=invoice.id, description=(plan.product.name if plan.product else plan.name), quantity=subscription.quantity, unit_price=unit_price, tax_rate=Decimal(str(tax_rate)), line_total=amount))
    db.commit()
    db.refresh(invoice)
    return {"id": invoice.id, "number": invoice.number, "amount_due": str(invoice.amount_due), "currency": invoice.currency, "status": invoice.status}

@router.post("/invoices/{invoice_id}/payments", status_code=201)
def record_payment(invoice_id: int, data: PaymentRecord, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "staff"))):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == "paid":
        raise HTTPException(status_code=409, detail="Invoice is already paid")
    if invoice.status not in {"open", "confirmed"}:
        raise HTTPException(status_code=409, detail="Only open or confirmed invoices can be paid")
    if db.scalar(select(Payment).where(Payment.provider_reference == data.provider_reference)):
        raise HTTPException(status_code=409, detail="Payment reference has already been recorded")
    payment = Payment(invoice_id=invoice.id, amount=invoice.amount_due, currency=invoice.currency, status="succeeded", provider_reference=data.provider_reference, method=data.method, note=data.note)
    invoice.status = "paid"
    db.add(payment)
    db.commit()
    return {"invoice_id": invoice.id, "status": "paid", "payment_reference": data.provider_reference, "amount": str(invoice.amount_due), "currency": invoice.currency}

@router.post("/invoices/{invoice_id}/razorpay-order", status_code=201)
def create_razorpay_order(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    subscription = db.get(Subscription, invoice.subscription_id)
    if not subscription:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if user.role == "customer" and subscription.customer_id != user.id:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if user.role not in {"customer", "staff", "admin"}:
        raise HTTPException(status_code=403, detail="This account cannot pay invoices")
    if invoice.status not in {"open", "confirmed"}:
        raise HTTPException(status_code=409, detail="Only unpaid invoices can be paid")
    if invoice.currency.upper() != "INR":
        raise HTTPException(status_code=422, detail="Razorpay checkout is currently enabled for INR invoices only")
    if invoice.amount_due <= 0:
        raise HTTPException(status_code=422, detail="Invoice total must be greater than zero")
    _razorpay_configured()

    existing = db.scalar(select(Payment).where(Payment.invoice_id == invoice.id, Payment.method == "razorpay", Payment.status == "pending").order_by(Payment.id.desc()))
    order_id = existing.provider_reference if existing else None
    if not order_id:
        order = _razorpay_request("POST", "/orders", {
            "amount": _amount_in_paise(invoice.amount_due),
            "currency": invoice.currency.upper(),
            "receipt": f"INV-{invoice.id}",
            "notes": {"invoice_id": str(invoice.id)},
        })
        order_id = order["id"]
        db.add(Payment(invoice_id=invoice.id, amount=invoice.amount_due, currency=invoice.currency, status="pending", provider_reference=order_id, method="razorpay", note=f"razorpay_order_id={order_id}"))
        db.commit()
    customer = db.get(User, subscription.customer_id)
    return {
        "key_id": settings.razorpay_key_id,
        "order_id": order_id,
        "amount": _amount_in_paise(invoice.amount_due),
        "currency": invoice.currency.upper(),
        "name": "Brightloop",
        "description": f"Invoice {invoice.number}",
        "prefill": {"name": customer.full_name, "email": customer.email},
    }

def _pending_razorpay_payment(db: Session, order_id: str) -> Payment:
    payment = db.scalar(select(Payment).where(Payment.method == "razorpay", Payment.note == f"razorpay_order_id={order_id}").order_by(Payment.id.desc()))
    if not payment:
        raise HTTPException(status_code=404, detail="Razorpay order not found")
    return payment

def _mark_razorpay_captured(db: Session, payment: Payment, entity: dict) -> Invoice:
    invoice = db.get(Invoice, payment.invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    payment_id = entity.get("id")
    if not payment_id:
        raise HTTPException(status_code=409, detail="Razorpay payment identifier is missing")
    if payment.status == "succeeded" and payment.provider_reference == payment_id:
        return invoice
    if entity.get("status") != "captured":
        raise HTTPException(status_code=409, detail="Razorpay has not captured this payment")
    if entity.get("order_id") != payment.note.removeprefix("razorpay_order_id="):
        raise HTTPException(status_code=409, detail="Payment does not match the invoice order")
    if int(entity.get("amount", -1)) != _amount_in_paise(invoice.amount_due) or str(entity.get("currency", "")).upper() != invoice.currency.upper():
        raise HTTPException(status_code=409, detail="Captured payment amount does not match this invoice")
    duplicate = db.scalar(select(Payment).where(Payment.provider_reference == payment_id, Payment.id != payment.id))
    if duplicate:
        raise HTTPException(status_code=409, detail="Razorpay payment was already applied to another invoice")
    payment.status = "succeeded"
    payment.provider_reference = payment_id
    payment.method = "razorpay"
    payment.amount = invoice.amount_due
    payment.currency = invoice.currency
    payment.note = f"razorpay_order_id={entity['order_id']}"
    invoice.status = "paid"
    db.commit()
    return invoice

@router.post("/invoices/razorpay/verify")
def verify_razorpay_payment(data: RazorpayVerify, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _razorpay_configured()
    payment = _pending_razorpay_payment(db, data.razorpay_order_id)
    invoice = db.get(Invoice, payment.invoice_id)
    subscription = db.get(Subscription, invoice.subscription_id) if invoice else None
    if not invoice or not subscription or (user.role == "customer" and subscription.customer_id != user.id):
        raise HTTPException(status_code=404, detail="Invoice not found")
    message = f"{data.razorpay_order_id}|{data.razorpay_payment_id}".encode()
    expected = hmac.new(settings.razorpay_key_secret.encode(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, data.razorpay_signature):
        raise HTTPException(status_code=400, detail="Razorpay payment signature is invalid")
    payment_details = _razorpay_request("GET", f"/payments/{data.razorpay_payment_id}")
    if payment_details.get("id") != data.razorpay_payment_id:
        raise HTTPException(status_code=409, detail="Razorpay payment could not be verified")
    invoice = _mark_razorpay_captured(db, payment, payment_details)
    return {"invoice_id": invoice.id, "status": invoice.status, "amount": str(invoice.amount_due), "currency": invoice.currency}

@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: FastAPIRequest, db: Session = Depends(get_db)):
    if not settings.razorpay_webhook_secret:
        raise HTTPException(status_code=503, detail="Razorpay webhook secret is not configured")
    raw_body = await request.body()
    supplied = request.headers.get("x-razorpay-signature", "")
    expected = hmac.new(settings.razorpay_webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=400, detail="Invalid Razorpay webhook signature")
    try:
        event = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid Razorpay webhook payload")
    if event.get("event") != "payment.captured":
        return {"status": "ignored"}
    entity = event.get("payload", {}).get("payment", {}).get("entity", {})
    order_id = entity.get("order_id")
    if not order_id:
        return {"status": "ignored"}
    payment = db.scalar(select(Payment).where(Payment.method == "razorpay", Payment.note == f"razorpay_order_id={order_id}").order_by(Payment.id.desc()))
    if not payment:
        return {"status": "ignored"}
    _mark_razorpay_captured(db, payment, entity)
    return {"status": "processed"}

@router.patch("/invoices/{invoice_id}/status")
def update_invoice_status(invoice_id: int, status: str, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "staff"))):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    transitions = {"open": {"confirmed", "cancelled"}, "draft": {"confirmed", "cancelled"}, "confirmed": {"cancelled"}}
    if status not in transitions.get(invoice.status, set()):
        raise HTTPException(status_code=409, detail=f"Cannot transition invoice from {invoice.status} to {status}")
    invoice.status = status
    db.commit()
    return {"id": invoice.id, "status": invoice.status}

@router.get("/invoices/{invoice_id}/print", response_class=HTMLResponse)
def print_invoice(invoice_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    subscription = db.get(Subscription, invoice.subscription_id)
    if user.role == "customer" and subscription.customer_id != user.id:
        raise HTTPException(status_code=404, detail="Invoice not found")
    customer = db.get(User, subscription.customer_id)
    safe = lambda value: escape(str(value if value is not None else ""), quote=True)
    issued_at = invoice.created_at.strftime("%d %b %Y") if invoice.created_at else "—"
    due_at = invoice.due_at.strftime("%d %b %Y") if invoice.due_at else "—"
    period = invoice.period_key if not invoice.period_key.startswith("manual-") else "One-time invoice"
    if len(invoice.lines) == 1:
        line = invoice.lines[0]
        line_rows = (
            f"<tr><td>{safe(line.description)}</td><td class='number'>{line.quantity}</td>"
            f"<td class='number'>{safe(invoice.currency)} {line.unit_price:,.2f}</td>"
            f"<td class='number'>{safe(invoice.currency)} {invoice.subtotal:,.2f}</td></tr>"
        )
    else:
        line_rows = "".join(
            f"<tr><td>{safe(line.description)}</td><td class='number'>{line.quantity}</td>"
            f"<td class='number'>{safe(invoice.currency)} {line.unit_price:,.2f}</td>"
            f"<td class='number'>{safe(invoice.currency)} {(line.unit_price * line.quantity):,.2f}</td></tr>"
            for line in invoice.lines
        ) or "<tr><td colspan='4' class='empty'>No line items were saved for this invoice.</td></tr>"
    tax_rates = {str(line.tax_rate) for line in invoice.lines}
    tax_label = f"Tax ({next(iter(tax_rates))}%)" if len(tax_rates) == 1 and next(iter(tax_rates)) != "0.00" else "Tax"
    status = safe(invoice.status.replace("_", " ").title())
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Invoice {safe(invoice.number)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f6f5;color:#18251f;font:14px/1.5 Arial,Helvetica,sans-serif}}
.sheet{{width:min(850px,calc(100% - 40px));margin:38px auto;padding:52px 58px;background:#fff;box-shadow:0 14px 44px #16271d18}}
header{{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;padding-bottom:26px;border-bottom:2px solid #236b5a}}
.brand{{margin:0;color:#173c31;font-size:28px;letter-spacing:-1px}}.muted{{color:#66766d}}.eyebrow{{margin:5px 0 0;color:#718078;font-size:11px;letter-spacing:1.3px;text-transform:uppercase}}
.invoice-title{{margin:0 0 5px;text-align:right;font-size:23px}}.status{{display:inline-block;margin-top:5px;padding:4px 10px;border-radius:20px;background:#edf5df;color:#476c2b;font-size:11px;font-weight:bold}}
.details{{display:grid;grid-template-columns:1fr 1fr;gap:32px;margin:30px 0}}.details h2{{margin:0 0 8px;color:#718078;font-size:10px;letter-spacing:1px;text-transform:uppercase}}
.details p{{margin:3px 0}}.meta{{display:grid;grid-template-columns:auto auto;gap:5px 14px;justify-content:end;text-align:right}}.meta span{{color:#718078}}.meta strong{{font-weight:600}}
table{{width:100%;border-collapse:collapse;margin-top:25px}}thead{{background:#f2f6f3}}th{{padding:12px 10px;text-align:left;color:#53645b;font-size:10px;letter-spacing:.6px;text-transform:uppercase}}td{{padding:13px 10px;border-bottom:1px solid #e9eeeb}}.number{{text-align:right;white-space:nowrap}}
.totals{{width:min(340px,100%);margin:20px 0 0 auto}}.totals div{{display:flex;justify-content:space-between;padding:6px 0;color:#59675f}}.totals .grand{{margin-top:8px;padding-top:13px;border-top:1px solid #ccd8d0;color:#173c31;font-size:18px;font-weight:bold}}
.footer{{margin-top:45px;padding-top:14px;border-top:1px solid #e9eeeb;color:#87928c;font-size:11px}}.print-button{{display:block;margin:26px auto 0;border:0;border-radius:7px;background:#236b5a;color:#fff;padding:11px 24px;font-weight:bold;cursor:pointer}}
@page{{size:A4;margin:16mm}}@media print{{body{{background:#fff;font-size:12px}}.sheet{{width:auto;margin:0;padding:0;box-shadow:none}}.print-button{{display:none}}.footer{{margin-top:28px}}}}
@media(max-width:600px){{.sheet{{width:100%;margin:0;padding:28px 20px}}header{{gap:12px}}.brand{{font-size:23px}}.details{{grid-template-columns:1fr;gap:18px}}.meta{{justify-content:start;text-align:left}}table{{font-size:12px}}th,td{{padding:9px 6px}}}}
</style></head><body><main class="sheet">
<header><div><h1 class="brand">Brightloop</h1><p class="eyebrow">Subscription billing</p></div><div><h2 class="invoice-title">Invoice {safe(invoice.number)}</h2><span class="status">{status}</span></div></header>
<section class="details"><div><h2>Bill to</h2><p><strong>{safe(customer.full_name)}</strong></p><p class="muted">{safe(customer.email)}</p></div><div class="meta"><span>Issued</span><strong>{safe(issued_at)}</strong><span>Due</span><strong>{safe(due_at)}</strong><span>Subscription</span><strong>{safe(subscription.number or f'#{subscription.id}')}</strong><span>Billing period</span><strong>{safe(period)}</strong></div></section>
<table><thead><tr><th>Description</th><th class="number">Qty</th><th class="number">Unit price</th><th class="number">Amount</th></tr></thead><tbody>{line_rows}</tbody></table>
<section class="totals"><div><span>Subtotal</span><strong>{safe(invoice.currency)} {invoice.subtotal:,.2f}</strong></div><div><span>{safe(tax_label)}</span><strong>{safe(invoice.currency)} {invoice.tax_amount:,.2f}</strong></div><div class="grand"><span>Invoice total</span><strong>{safe(invoice.currency)} {invoice.amount_due:,.2f}</strong></div></section>
<p class="footer">Thank you for your business. Please contact your workspace administrator with any questions about this invoice.</p><button class="print-button" onclick="window.print()">Print invoice</button>
</main></body></html>""")

@router.post("/invoices/{invoice_id}/send")
def send_invoice(invoice_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "staff"))):
    if not settings.smtp_host:
        raise HTTPException(status_code=503, detail="Invoice email is unavailable until SMTP is configured")
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    subscription = db.get(Subscription, invoice.subscription_id)
    customer = db.get(User, subscription.customer_id)
    message = EmailMessage()
    message["Subject"] = f"Brightloop invoice {invoice.number}"
    message["From"] = settings.mail_from
    message["To"] = customer.email
    message.set_content(f"Invoice {invoice.number} for {invoice.currency} {invoice.amount_due} is ready. Due date: {invoice.due_at:%Y-%m-%d}.")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)
    if invoice.status == "draft":
        invoice.status = "confirmed"
        db.commit()
    return {"id": invoice.id, "sent_to": customer.email, "status": invoice.status}

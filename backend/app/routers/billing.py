from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from email.message import EmailMessage
import smtplib
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from backend.app.core import settings
from backend.app.database import get_db
from backend.app.dependencies import current_user, require_roles
from backend.app.models import Discount, Invoice, InvoiceLine, Payment, Subscription, TaxRule, User

router = APIRouter(prefix="/api", tags=["billing"])
class InvoiceCreate(BaseModel):
    subscription_id: int
    discount_code: str | None = Field(default=None, max_length=40)
class PaymentRecord(BaseModel):
    provider_reference: str = Field(min_length=1, max_length=120)
    method: str = Field(default="manual", pattern="^(cash|bank_transfer|card|upi|other|manual)$")
    note: str = Field(default="", max_length=500)

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
    tax_rate = db.scalar(select(func.coalesce(func.sum(TaxRule.rate_percent), 0)).where(TaxRule.is_active.is_(True))) or Decimal("0")
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
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><title>{invoice.number}</title><style>body{{font:16px Arial;max-width:760px;margin:48px auto;color:#172234}}header{{display:flex;justify-content:space-between;border-bottom:2px solid #236b5a;padding-bottom:20px}}table{{width:100%;margin-top:36px;border-collapse:collapse}}td{{padding:12px;border-bottom:1px solid #ddd}}.total{{text-align:right;font-size:22px;font-weight:bold;margin-top:24px}}@media print{{button{{display:none}}}}</style></head><body><header><div><h1>Brightloop</h1><p>Subscription invoice</p></div><div><h2>{invoice.number}</h2><p>{invoice.status.title()}</p></div></header><p>Bill to: {customer.full_name} · {customer.email}</p><table><tr><td>Subscription #{subscription.id}</td><td>{invoice.period_key}</td><td>{invoice.currency} {invoice.subtotal}</td></tr><tr><td colspan='2'>Tax</td><td>{invoice.currency} {invoice.tax_amount}</td></tr></table><p class='total'>Total due: {invoice.currency} {invoice.amount_due}</p><button onclick='window.print()'>Print invoice</button></body></html>""")

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

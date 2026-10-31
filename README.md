# Brightloop Billing

A subscription operations system for the Odoo Makeathon 9.0 brief. It brings catalog and plan setup, subscriptions, recurring invoice generation, discounts, payment recording, and reporting into one app.

## Stack

- FastAPI and SQLAlchemy 2
- PostgreSQL in deployment; SQLite is supported for local development
- Alembic schema setup
- Vanilla HTML, CSS, and JavaScript frontend
- JWT sessions and Argon2 password hashing

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set a strong `JWT_SECRET_KEY`. Run schema setup and start the app:

```bash
python -m alembic upgrade head
uvicorn backend.app.main:app --reload
```

Create the first administrator through the secure local CLI:

```bash
python -m backend.app.create_admin
```

Then open `http://localhost:8000`. Customer accounts can register from the sign-in dialog. Admins can create staff accounts through `POST /api/auth/staff`.

## Recurring billing

The billing runner is idempotent by subscription and billing period. Run it from a system scheduler (for example, a daily cron task) with the same environment as the API:

```bash
python -m backend.app.billing_runner
```

For password reset email, configure `SMTP_HOST`, `SMTP_PORT`, credentials, `MAIL_FROM`, and `PUBLIC_APP_URL`. Without SMTP configured, the reset endpoint deliberately returns a generic response and does not expose reset tokens.

## Main API areas

- `/api/auth`: customer registration/login, staff provisioning, password reset
- `/api/products`, `/api/plans`, `/api/taxes`: catalog and billing setup
- `/api/subscriptions`, `/api/quotations`: subscription and quotation flows
- `/api/discounts`, `/api/invoices`: discounts and invoice lifecycle
- `/api/dashboard`: financial summary, invoice list, and revenue trend

Interactive API docs are available at `/docs`.

## Validation

Run the automated suite with `python -m pytest -q`. It covers the dashboard shell, registration/login, subscription-to-invoice-to-payment flow, role checks, and recurring billing idempotency.

## Current integration boundaries

Payment recording is internal bookkeeping; no live card processor is connected. Invoice sending requires SMTP configuration. The initial demo focuses on INR totals and monthly recurring revenue. Review taxes and any country-specific billing requirements before production use.

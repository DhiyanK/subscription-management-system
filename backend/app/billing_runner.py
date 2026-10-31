from backend.app.database import SessionLocal
from backend.app.services.recurring_billing import generate_due_invoices

if __name__ == "__main__":
    with SessionLocal() as session:
        print(f"Generated {generate_due_invoices(session)} invoice(s).")

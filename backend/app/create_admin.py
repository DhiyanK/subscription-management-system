from getpass import getpass
from sqlalchemy import select
from backend.app.database import SessionLocal
from backend.app.models import User
from backend.app.security import hash_password

if __name__ == "__main__":
    email = input("Admin email: ").strip().lower()
    name = input("Admin name: ").strip()
    password = getpass("Password (uppercase, lowercase, special character; 9+ chars; input hidden): ")
    print("Password received. Creating administrator...", flush=True)
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)):
            raise SystemExit("An account with that email already exists.")
        db.add(User(email=email, full_name=name, password_hash=hash_password(password), role="admin"))
        db.commit()
    print("Administrator created.")

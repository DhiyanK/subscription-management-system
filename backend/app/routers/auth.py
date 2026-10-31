from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets
import smtplib
from email.message import EmailMessage
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.core import settings
from backend.app.database import get_db
from backend.app.dependencies import current_user, require_roles
from backend.app.models import User
from backend.app.schemas import LoginInput, RegisterInput, TokenOut, UserOut
from backend.app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["authentication"])
class StaffCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=9, max_length=128)
    @field_validator("password")
    @classmethod
    def password_policy(cls, value: str) -> str:
        import re
        if not re.search(r"[A-Z]", value) or not re.search(r"[a-z]", value) or not re.search(r"[^A-Za-z0-9]", value):
            raise ValueError("Password must include uppercase, lowercase, and a special character")
        return value
class PasswordResetRequest(BaseModel):
    email: EmailStr
class PasswordReset(BaseModel):
    token: str = Field(min_length=32, max_length=128)
    new_password: str = Field(min_length=9, max_length=128)

@router.post("/register", response_model=UserOut, status_code=201)
def register(data: RegisterInput, db: Session = Depends(get_db)):
    email = str(data.email).lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(email=email, full_name=data.full_name.strip(), password_hash=hash_password(data.password))
    db.add(user); db.commit(); db.refresh(user)
    return user

@router.post("/staff", response_model=UserOut, status_code=201)
def create_staff(data: StaffCreate, db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    email = str(data.email).lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(email=email, full_name=data.full_name.strip(), password_hash=hash_password(data.password), role="staff")
    db.add(user); db.commit(); db.refresh(user)
    return user

@router.post("/login", response_model=TokenOut)
def login(data: LoginInput, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == str(data.email).lower()))
    if not user or not user.is_active or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
    return TokenOut(access_token=create_access_token(str(user.id), user.role))

@router.post("/password-reset/request")
def request_password_reset(data: PasswordResetRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == str(data.email).lower(), User.is_active.is_(True)))
    if user:
        token = secrets.token_urlsafe(32)
        user.reset_token_hash = sha256(token.encode()).hexdigest()
        user.reset_token_expires = datetime.now(timezone.utc) + timedelta(minutes=30)
        db.commit()
        if settings.smtp_host:
            message = EmailMessage()
            message["Subject"] = "Reset your Brightloop Billing password"
            message["From"] = settings.mail_from
            message["To"] = user.email
            message.set_content(f"Use this one-time link within 30 minutes: {settings.public_app_url}/reset-password?token={token}")
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
                smtp.starttls()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
    return {"message": "If the account exists, password reset instructions have been sent."}

@router.post("/password-reset/confirm")
def reset_password(data: PasswordReset, db: Session = Depends(get_db)):
    import re
    value = data.new_password
    if not re.search(r"[A-Z]", value) or not re.search(r"[a-z]", value) or not re.search(r"[^A-Za-z0-9]", value):
        raise HTTPException(status_code=422, detail="Password must include uppercase, lowercase, and a special character")
    hashed = sha256(data.token.encode()).hexdigest()
    user = db.scalar(select(User).where(User.reset_token_hash == hashed))
    if not user or not user.reset_token_expires or user.reset_token_expires < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Reset link is invalid or expired")
    user.password_hash = hash_password(value)
    user.reset_token_hash = None
    user.reset_token_expires = None
    db.commit()
    return {"message": "Password updated successfully."}

@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user

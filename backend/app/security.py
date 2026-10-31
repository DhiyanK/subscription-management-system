from datetime import datetime, timedelta, timezone
from jose import jwt
from pwdlib import PasswordHash
from backend.app.core import settings

ALGORITHM = "HS256"
password_hash = PasswordHash.recommended()
def hash_password(password: str) -> str:
    return password_hash.hash(password)
def verify_password(password: str, encoded_hash: str) -> bool:
    return password_hash.verify(password, encoded_hash)
def create_access_token(subject: str, role: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({"sub": subject, "role": role, "exp": expires}, settings.jwt_secret_key, algorithm=ALGORITHM)

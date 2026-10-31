from decimal import Decimal
from datetime import datetime
import re
from pydantic import field_validator
from pydantic import BaseModel, ConfigDict, EmailStr, Field

class RegisterInput(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=9, max_length=128)
    @field_validator("password")
    @classmethod
    def password_policy(cls, value: str) -> str:
        if not re.search(r"[A-Z]", value) or not re.search(r"[a-z]", value) or not re.search(r"[^A-Za-z0-9]", value):
            raise ValueError("Password must include uppercase, lowercase, and a special character")
        return value
class LoginInput(BaseModel):
    email: EmailStr
    password: str
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    full_name: str
    role: str
class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
class PlanCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = ""
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    interval: str = Field(pattern="^(daily|weekly|monthly|yearly)$")
    product_id: int | None = None
    minimum_quantity: int = Field(default=1, ge=1)
    available_from: datetime | None = None
    available_until: datetime | None = None
    auto_close: bool = True
    allow_pause: bool = True
    allow_early_close: bool = True
class PlanOut(PlanCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    is_active: bool

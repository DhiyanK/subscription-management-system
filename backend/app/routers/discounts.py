from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.database import get_db
from backend.app.dependencies import require_roles
from backend.app.models import Discount, User

router = APIRouter(prefix="/api/discounts", tags=["discounts"])
class DiscountCreate(BaseModel):
    code: str = Field(min_length=3, max_length=40, pattern="^[A-Za-z0-9_-]+$")
    percent_off: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    amount_off: Decimal = Field(default=Decimal("0"), ge=0)
    minimum_quantity: int = Field(default=1, ge=1)
    minimum_amount: Decimal = Field(default=Decimal("0"), ge=0)
    product_id: int | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    usage_limit: int | None = Field(default=None, ge=1)
    @model_validator(mode="after")
    def validate_discount(self):
        if (self.percent_off > 0) == (self.amount_off > 0):
            raise ValueError("Set exactly one of percent_off or amount_off")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self

@router.get("")
def list_discounts(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "staff"))):
    return [{"id": d.id, "code": d.code, "percent_off": str(d.percent_off), "amount_off": str(d.amount_off), "usage_count": d.usage_count, "usage_limit": d.usage_limit, "active": d.active, "starts_at": d.starts_at, "ends_at": d.ends_at} for d in db.scalars(select(Discount).order_by(Discount.code))]
@router.post("", status_code=201)
def create_discount(data: DiscountCreate, db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    code = data.code.upper()
    if db.scalar(select(Discount.id).where(Discount.code == code)):
        raise HTTPException(status_code=409, detail="Discount code already exists")
    discount = Discount(code=code, **data.model_dump(exclude={"code"}))
    db.add(discount); db.commit(); db.refresh(discount)
    return {"id": discount.id, "code": discount.code, "active": discount.active}
@router.patch("/{discount_id}/deactivate")
def deactivate_discount(discount_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    discount = db.get(Discount, discount_id)
    if not discount: raise HTTPException(status_code=404, detail="Discount not found")
    discount.active = False; db.commit()
    return {"id": discount.id, "active": False}

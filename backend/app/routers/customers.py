from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.database import get_db
from backend.app.dependencies import require_roles
from backend.app.models import User

router = APIRouter(prefix="/api/customers", tags=["customers"])
@router.get("")
def customers(search: str = "", limit: int = 50, offset: int = 0, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "staff"))):
    limit = max(1, min(limit, 100))
    query = select(User).where(User.role == "customer").order_by(User.created_at.desc()).offset(offset).limit(limit)
    if search:
        query = query.where(User.email.ilike(f"%{search}%") | User.full_name.ilike(f"%{search}%"))
    return [{"id": user.id, "name": user.full_name, "email": user.email, "active": user.is_active, "created_at": user.created_at} for user in db.scalars(query)]

@router.get("/staff")
def staff_members(limit: int = 50, offset: int = 0, db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    limit = max(1, min(limit, 100))
    query = select(User).where(User.role == "staff").order_by(User.created_at.desc()).offset(offset).limit(limit)
    return [{"id": user.id, "name": user.full_name, "email": user.email, "active": user.is_active, "created_at": user.created_at} for user in db.scalars(query)]

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.database import get_db
from backend.app.dependencies import require_roles
from backend.app.models import Plan, User
from backend.app.schemas import PlanCreate, PlanOut

router = APIRouter(prefix="/api/plans", tags=["plans"])
@router.get("", response_model=list[PlanOut])
def list_plans(db: Session = Depends(get_db)):
    return list(db.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.price)))
@router.post("", response_model=PlanOut, status_code=201)
def create_plan(data: PlanCreate, db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    plan = Plan(**data.model_dump())
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan

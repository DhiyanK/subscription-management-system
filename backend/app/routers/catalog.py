from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.database import get_db
from backend.app.dependencies import current_user, require_roles
from backend.app.models import Product, ProductVariant, TaxRule, User

router = APIRouter(prefix="/api", tags=["catalog"])
class ProductInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    product_type: str = Field(default="service", pattern="^(service|goods|digital)$")
    sales_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    cost_price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    notes: str = ""
class VariantInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    price_adjustment: Decimal = Field(default=Decimal("0"), max_digits=12, decimal_places=2)
class TaxInput(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    rate_percent: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)

@router.get("/products")
def products(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "staff"))):
    return [{"id": p.id, "name": p.name, "product_type": p.product_type, "sales_price": str(p.sales_price), "cost_price": str(p.cost_price), "notes": p.notes, "variants": [{"id": v.id, "name": v.name, "price_adjustment": str(v.price_adjustment)} for v in p.variants]} for p in db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.name))]
@router.post("/products", status_code=201)
def create_product(data: ProductInput, db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    product = Product(**data.model_dump())
    db.add(product); db.commit(); db.refresh(product)
    return {"id": product.id, "name": product.name, "sales_price": str(product.sales_price)}
@router.post("/products/{product_id}/variants", status_code=201)
def create_variant(product_id: int, data: VariantInput, db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    if not db.get(Product, product_id): raise HTTPException(status_code=404, detail="Product not found")
    variant = ProductVariant(product_id=product_id, **data.model_dump())
    db.add(variant); db.commit(); db.refresh(variant)
    return {"id": variant.id, "name": variant.name, "price_adjustment": str(variant.price_adjustment)}
@router.get("/taxes")
def taxes(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "staff"))):
    return [{"id": t.id, "name": t.name, "rate_percent": str(t.rate_percent)} for t in db.scalars(select(TaxRule).where(TaxRule.is_active.is_(True)).order_by(TaxRule.name))]

@router.get("/tax-options")
def tax_options(db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Return active tax rates for invoice and subscription checkout selectors."""
    return [{"id": t.id, "name": t.name, "rate_percent": str(t.rate_percent)} for t in db.scalars(select(TaxRule).where(TaxRule.is_active.is_(True)).order_by(TaxRule.name))]
@router.post("/taxes", status_code=201)
def create_tax(data: TaxInput, db: Session = Depends(get_db), _: User = Depends(require_roles("admin"))):
    tax = TaxRule(**data.model_dump())
    db.add(tax); db.commit(); db.refresh(tax)
    return {"id": tax.id, "name": tax.name, "rate_percent": str(tax.rate_percent)}

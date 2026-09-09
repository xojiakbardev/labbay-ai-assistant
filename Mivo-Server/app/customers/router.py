import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.businesses.models import Business
from app.common.tenancy import get_current_business
from app.core.db import get_db
from app.customers.models import Customer

router = APIRouter(prefix="/customers", tags=["customers"])


class CustomerUpdateIn(BaseModel):
    username: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)


class CustomerOut(BaseModel):
    id: uuid.UUID
    business_id: uuid.UUID
    ig_scoped_id: str
    username: str | None
    name: str | None
    phone: str | None

    class Config:
        from_attributes = True


@router.patch("/{customer_id}", response_model=CustomerOut)
async def update_customer(
    customer_id: uuid.UUID,
    payload: CustomerUpdateIn,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    customer = await db.scalar(
        select(Customer).where(
            Customer.business_id == business.id,
            Customer.id == customer_id,
        )
    )
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found.")

    if payload.username is not None:
        clean_user = payload.username.strip().lstrip("@")
        customer.username = clean_user if clean_user else None

    if payload.phone is not None:
        clean_phone = payload.phone.strip()
        customer.phone = clean_phone if clean_phone else None

    await db.commit()
    await db.refresh(customer)
    return customer

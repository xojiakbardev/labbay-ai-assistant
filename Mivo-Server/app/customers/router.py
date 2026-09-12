import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.businesses.models import Business
from app.common.tenancy import get_current_business
from app.core.db import get_db
from app.customers.models import Customer
from app.leads.models import Lead
from app.leads.scoring import extract_valid_phone

router = APIRouter(prefix="/customers", tags=["customers"])


class CustomerUpdateIn(BaseModel):
    username: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    ig_scoped_id: str
    username: str | None
    name: str | None
    phone: str | None


@router.patch("/{customer_id}", response_model=CustomerOut)
async def update_customer(
    customer_id: uuid.UUID,
    payload: CustomerUpdateIn,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """Owner edits. An empty string clears a field; a phone number is stored
    in the same normalized form as captured ones (and rejected if it isn't a
    phone number), and kept in step on the customer's lead."""
    customer = await db.scalar(
        select(Customer).where(Customer.business_id == business.id, Customer.id == customer_id)
    )
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found.")

    if payload.username is not None:
        customer.username = payload.username.strip().lstrip("@") or None

    if payload.name is not None:
        customer.name = payload.name.strip() or None

    if payload.phone is not None:
        raw = payload.phone.strip()
        phone = None
        if raw:
            phone = extract_valid_phone(raw)
            if phone is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Not a valid phone number.")
        customer.phone = phone
        lead = await db.scalar(
            select(Lead).where(Lead.business_id == business.id, Lead.customer_id == customer.id)
        )
        if lead is not None:
            lead.phone = phone

    await db.commit()
    await db.refresh(customer)
    return customer

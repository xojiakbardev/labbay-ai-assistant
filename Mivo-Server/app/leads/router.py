import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.businesses.models import Business
from app.common.tenancy import get_current_business
from app.customers.models import Customer
from app.core.db import get_db
from app.leads.models import Lead
from app.leads.schemas import LeadOut

router = APIRouter(prefix="/leads", tags=["leads"])


@router.get("", response_model=list[LeadOut])
async def list_leads(
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Lead, Customer)
        .join(Customer, Lead.customer_id == Customer.id)
        .where(Lead.business_id == business.id, Customer.is_sandbox.is_(False))
        .order_by(Lead.updated_at.desc(), Lead.id)
        .limit(limit)
        .offset(offset)
    )
    items = []
    for lead, cust in result.all():
        if cust.username:
            username = f"@{cust.username.lstrip('@')}"
        elif cust.name:
            username = cust.name
        else:
            username = "Instagram foydalanuvchisi"
        items.append(
            LeadOut(
                id=lead.id,
                customer_id=lead.customer_id,
                customer_username=username,
                conversation_id=lead.conversation_id,
                status=lead.status,
                score=lead.score,
                phone=lead.phone,
                interested_products=lead.interested_products or [],
                summary=lead.summary,
                qualification_reason=lead.qualification_reason,
                qualification_reasons=lead.qualification_reasons or {},
                summaries=lead.summaries or {},
                hot_notified_at=lead.hot_notified_at,
                created_at=lead.created_at,
                updated_at=lead.updated_at,
            )
        )
    return items


@router.get("/{lead_id}", response_model=LeadOut)
async def get_lead(
    lead_id: uuid.UUID,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Lead, Customer)
        .join(Customer, Lead.customer_id == Customer.id)
        .where(Lead.business_id == business.id, Lead.id == lead_id)
    )
    res = result.first()
    if res is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found.")
    lead, cust = res
    if cust.username:
        username = f"@{cust.username.lstrip('@')}"
    elif cust.name:
        username = cust.name
    else:
        username = "Instagram foydalanuvchisi"
    return LeadOut(
        id=lead.id,
        customer_id=lead.customer_id,
        customer_username=username,
        conversation_id=lead.conversation_id,
        status=lead.status,
        score=lead.score,
        phone=lead.phone,
        interested_products=lead.interested_products or [],
        summary=lead.summary,
        qualification_reason=lead.qualification_reason,
        qualification_reasons=lead.qualification_reasons or {},
        summaries=lead.summaries or {},
        hot_notified_at=lead.hot_notified_at,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
    )

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider.base import LLMProvider
from app.ai.provider.factory import get_llm_provider
from app.businesses.models import Business
from app.common.tenancy import get_current_business
from app.core.db import get_db
from app.products import crud
from app.products.ingestion import service as ingestion_service
from app.products.ingestion.service import IngestionError
from app.products.schemas import ImageOut, MediaUploadOut, ProductCreate, ProductOut, ProductUpdate
from app.storage.r2 import R2Storage, get_r2_storage, validate_and_read_upload

router = APIRouter(prefix="/products", tags=["products"])

# A pasted catalogue, not a novel — and every character is sent to the LLM.
MAX_IMPORT_TEXT_CHARS = 50_000


class ImportPreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_IMPORT_TEXT_CHARS)


class ImportPreviewResponse(BaseModel):
    products: list[ProductCreate]


class ImportConfirmRequest(BaseModel):
    products: list[ProductCreate] = Field(min_length=1, max_length=500)


async def _get_owned_product(
    product_id: uuid.UUID,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    product = await crud.get_product(db, business.id, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")
    return product


@router.get("", response_model=list[ProductOut])
async def list_products(
    business: Business = Depends(get_current_business), db: AsyncSession = Depends(get_db)
):
    return await crud.list_products(db, business.id)


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    body: ProductCreate,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    return await crud.create_product(db, business.id, body, source="manual")


@router.post("/media", response_model=MediaUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile,
    business: Business = Depends(get_current_business),
    storage: R2Storage = Depends(get_r2_storage),
):
    """Stores an image and returns its public URL — for variant photos and
    for a product that doesn't exist yet. The form then saves the URL; image
    bytes never travel inside product JSON."""
    data, content_type, extension = await validate_and_read_upload(file)
    key = storage.build_key(business.id, None, extension)
    return MediaUploadOut(url=await storage.upload_bytes(key, data, content_type))


@router.post("/import/preview", response_model=ImportPreviewResponse)
async def preview_product_import(
    body: ImportPreviewRequest,
    business: Business = Depends(get_current_business),
    provider: LLMProvider = Depends(get_llm_provider),
):
    """Extraction only — nothing is written to PostgreSQL until /import/confirm."""
    try:
        products = await ingestion_service.preview_import(body.text, provider, business_id=business.id)
    except IngestionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return ImportPreviewResponse(products=products)


@router.post("/import/confirm", response_model=list[ProductOut], status_code=status.HTTP_201_CREATED)
async def confirm_product_import(
    body: ImportConfirmRequest,
    background: BackgroundTasks,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    """Called only after the business owner has reviewed/edited the preview.
    All products are saved together or not at all; they're embedded for
    semantic search after the response."""
    products = await ingestion_service.confirm_import(db, business.id, body.products)
    background.add_task(crud.embed_products_later, [p.id for p in products])
    return products


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(product=Depends(_get_owned_product)):
    return product


@router.patch("/{product_id}", response_model=ProductOut)
async def update_product(
    body: ProductUpdate,
    product=Depends(_get_owned_product),
    db: AsyncSession = Depends(get_db),
):
    return await crud.update_product(db, product, body)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(product=Depends(_get_owned_product), db: AsyncSession = Depends(get_db)):
    await crud.delete_product(db, product)


@router.post("/{product_id}/images", response_model=ImageOut, status_code=status.HTTP_201_CREATED)
async def upload_product_image(
    file: UploadFile,
    is_primary: bool = False,
    product=Depends(_get_owned_product),
    db: AsyncSession = Depends(get_db),
    storage: R2Storage = Depends(get_r2_storage),
):
    data, content_type, extension = await validate_and_read_upload(file)
    key = storage.build_key(product.business_id, product.id, extension)
    url = await storage.upload_bytes(key, data, content_type)
    return await crud.add_product_image(db, product, r2_key=key, url=url, is_primary=is_primary)

"""Cloudflare R2 client wrapper (S3-compatible).

MVP uses backend-proxied uploads (multipart -> FastAPI -> R2), not presigned
direct-from-browser upload — simpler to validate server-side, sufficient at MVP
image volume (plan §12).
"""
import uuid
from functools import lru_cache

import boto3
from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB


class R2Storage:
    def __init__(self) -> None:
        settings = get_settings()
        self._bucket = settings.r2_bucket
        self._public_base_url = settings.r2_public_base_url.rstrip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
        )

    def build_key(self, business_id: uuid.UUID, product_id: uuid.UUID, filename: str) -> str:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
        return f"{business_id}/products/{product_id}/{uuid.uuid4()}.{ext}"

    def upload_bytes(self, key: str, data: bytes, content_type: str) -> str:
        self._client.put_object(
            Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
        )
        return f"{self._public_base_url}/{key}"


@lru_cache
def get_r2_storage() -> R2Storage:
    return R2Storage()


async def validate_and_read_upload(file: UploadFile) -> bytes:
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported file type '{file.content_type}'. Allowed: jpeg, png, webp.",
        )
    data = await file.read()
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File exceeds 5MB limit.")
    return data

"""Cloudflare R2 client wrapper (S3-compatible).

MVP uses backend-proxied uploads (multipart -> FastAPI -> R2), not presigned
direct-from-browser upload — simpler to validate server-side, sufficient at MVP
image volume (plan §12).
"""
import logging
import uuid
from functools import lru_cache

import boto3
from fastapi import HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings

logger = logging.getLogger("app.storage.r2")

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB
_CHUNK = 64 * 1024

# Images referenced by URL only (not stored by us) carry this key prefix, so
# nothing ever tries to delete them from the bucket.
EXTERNAL_KEY_PREFIX = "external/"

# The type is decided from the file's own bytes, never from the client's
# Content-Type header or filename.
_SIGNATURES: tuple[tuple[bytes, int, str, str], ...] = (
    (b"\xff\xd8\xff", 0, "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", 0, "image/png", "png"),
    (b"WEBP", 8, "image/webp", "webp"),
)


class R2Storage:
    def __init__(self) -> None:
        settings = get_settings()
        if not (settings.r2_account_id and settings.r2_access_key_id and settings.r2_public_base_url):
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "File storage is not configured.")
        self._bucket = settings.r2_bucket
        self._public_base_url = settings.r2_public_base_url.rstrip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
        )

    def build_key(self, business_id: uuid.UUID, product_id: uuid.UUID | None, extension: str) -> str:
        folder = str(product_id) if product_id else "unassigned"
        return f"{business_id}/products/{folder}/{uuid.uuid4()}.{extension}"

    async def upload_bytes(self, key: str, data: bytes, content_type: str) -> str:
        # boto3 is blocking; in the event loop it would stall every other
        # request (webhooks, SSE) for the length of the upload.
        await run_in_threadpool(
            self._client.put_object, Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
        )
        return f"{self._public_base_url}/{key}"

    def delete_keys(self, keys: list[str]) -> None:
        stored = [k for k in keys if k and not k.startswith(EXTERNAL_KEY_PREFIX)]
        if not stored:
            return
        response = self._client.delete_objects(
            Bucket=self._bucket, Delete={"Objects": [{"Key": k} for k in stored]}
        )
        errors = response.get("Errors") or []
        if errors:
            raise RuntimeError(f"R2 refused to delete {len(errors)} object(s): {errors[:5]}")


@lru_cache
def get_r2_storage() -> R2Storage:
    return R2Storage()


def stored_key_for(url: str) -> str:
    """The bucket key behind a URL we serve ourselves (uploads through
    /products/media or /products/{id}/images), or an `external/` placeholder
    for anyone else's URL — which is never deleted from the bucket."""
    base = get_settings().r2_public_base_url.rstrip("/")
    if base and url.startswith(base + "/"):
        return url[len(base) + 1 :]
    return f"{EXTERNAL_KEY_PREFIX}{uuid.uuid4()}"


async def delete_objects(keys: list[str]) -> None:
    """Removes stored files after their rows are gone. A failure leaves an
    orphaned object (storage cost, nothing user-visible) and is logged."""
    stored = sorted({k for k in keys if k and not k.startswith(EXTERNAL_KEY_PREFIX)})
    if not stored:
        return
    try:
        await run_in_threadpool(get_r2_storage().delete_keys, stored)
    except Exception:  # noqa: BLE001 — see docstring
        logger.exception("[r2] could not delete %d object(s): %s", len(stored), stored)


async def validate_and_read_upload(file: UploadFile) -> tuple[bytes, str, str]:
    """Reads at most MAX_FILE_SIZE_BYTES + 1 (never the whole body of an
    oversized upload into memory) and identifies the image by its magic
    bytes. Returns (data, content_type, extension)."""
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(_CHUNK):
        size += len(chunk)
        if size > MAX_FILE_SIZE_BYTES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "File exceeds 5MB limit.")
        chunks.append(chunk)
    data = b"".join(chunks)
    for signature, offset, content_type, extension in _SIGNATURES:
        if data[offset : offset + len(signature)] == signature and (content_type != "image/webp" or data[:4] == b"RIFF"):
            return data, content_type, extension
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported file type. Allowed: jpeg, png, webp.")

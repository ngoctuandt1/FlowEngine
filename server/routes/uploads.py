"""Upload endpoints for local assets used by composer jobs."""

from io import BytesIO
from pathlib import Path
import os
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

router = APIRouter(prefix="/api", tags=["uploads"])

UPLOAD_DIR = Path(os.environ.get("FLOW_UPLOAD_DIR", "./uploads")).resolve()
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
ALLOWED_FORMATS = {
    "PNG": ALLOWED_TYPES["image/png"],
    "JPEG": ALLOWED_TYPES["image/jpeg"],
    "WEBP": ALLOWED_TYPES["image/webp"],
}
GENERIC_CONTENT_TYPES = {"", "application/octet-stream"}


async def _read_limited_upload(file: UploadFile) -> bytes:
    payload = bytearray()
    total_bytes = 0

    while chunk := await file.read(READ_CHUNK_BYTES):
        total_bytes += len(chunk)
        if total_bytes > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File exceeds 10 MB limit")
        payload.extend(chunk)

    return bytes(payload)


def _sniff_image_suffix(payload: bytes) -> str:
    try:
        with Image.open(BytesIO(payload)) as image:
            image.load()
            detected_format = (image.format or "").upper()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(415, "Unsupported file type") from None

    suffix = ALLOWED_FORMATS.get(detected_format)
    if suffix is None:
        raise HTTPException(415, "Unsupported file type")
    return suffix


@router.post("/uploads")
async def upload_image(file: UploadFile = File(...)):
    """Store one image under FLOW_UPLOAD_DIR and return its server path."""
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_TYPES and content_type not in GENERIC_CONTENT_TYPES:
        raise HTTPException(415, "Unsupported file type")

    try:
        payload = await _read_limited_upload(file)
    finally:
        await file.close()

    suffix = _sniff_image_suffix(payload)

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    rel_path = Path("uploads") / f"{uuid.uuid4()}{suffix}"
    dest = UPLOAD_DIR / rel_path.name
    dest.write_bytes(payload)
    return {"path": rel_path.as_posix()}

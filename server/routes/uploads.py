"""Upload endpoints for local assets used by composer jobs."""

from pathlib import Path
import os
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile

router = APIRouter(prefix="/api", tags=["uploads"])

UPLOAD_DIR = Path(os.environ.get("FLOW_UPLOAD_DIR", "./uploads")).resolve()
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


@router.post("/uploads")
async def upload_image(file: UploadFile = File(...)):
    """Store one image under FLOW_UPLOAD_DIR and return its server path."""
    suffix = ALLOWED_TYPES.get(file.content_type or "")
    if suffix is None:
        raise HTTPException(415, "Unsupported file type")

    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "File exceeds 10 MB limit")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    rel_path = Path("uploads") / f"{uuid.uuid4()}{suffix}"
    dest = UPLOAD_DIR / rel_path.name
    dest.write_bytes(payload)
    return {"path": rel_path.as_posix()}

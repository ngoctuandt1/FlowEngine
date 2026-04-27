"""Text-to-speech endpoints backed by edge-tts."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from server.config import DATA_DIR

try:
    import edge_tts
except ImportError:  # pragma: no cover - exercised only when dependency is missing locally
    edge_tts = None


router = APIRouter(prefix="/api/tts", tags=["tts"])

VOICE_PREFIXES = (
    "vi-VN-",
    "en-US-",
    "en-GB-",
    "ja-JP-",
    "ko-KR-",
)
TTS_DIR = (DATA_DIR / "tts").resolve()
CHARS_PER_SECOND_ESTIMATE = 12.5


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    voice: str = "vi-VN-HoaiMyNeural"
    rate: str = "+0%"
    pitch: str = "+0Hz"

    @field_validator("voice")
    @classmethod
    def validate_voice_prefix(cls, value: str) -> str:
        if not value.startswith(VOICE_PREFIXES):
            allowed = ", ".join(f"{prefix}*" for prefix in VOICE_PREFIXES)
            raise ValueError(f"voice must match one of: {allowed}")
        return value


class TTSResponse(BaseModel):
    output_path: str
    duration_seconds_estimate: float
    voice: str


def _estimate_duration_seconds(text: str) -> float:
    return round(len(text) / CHARS_PER_SECOND_ESTIMATE, 2)


@router.post("", response_model=TTSResponse)
async def synthesize_tts(payload: TTSRequest) -> TTSResponse:
    """Synthesize speech to a local mp3 file under DATA_DIR/tts."""
    if edge_tts is None:
        raise HTTPException(500, "edge-tts dependency is not installed")

    TTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = (TTS_DIR / f"tts_{uuid4()}.mp3").resolve()
    try:
        output_path.relative_to(TTS_DIR)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise HTTPException(500, "Resolved TTS path escaped DATA_DIR/tts") from exc

    communicator = edge_tts.Communicate(
        text=payload.text,
        voice=payload.voice,
        rate=payload.rate,
        pitch=payload.pitch,
    )
    await communicator.save(str(output_path))

    return TTSResponse(
        output_path=str(output_path),
        duration_seconds_estimate=_estimate_duration_seconds(payload.text),
        voice=payload.voice,
    )


__all__ = [
    "CHARS_PER_SECOND_ESTIMATE",
    "TTS_DIR",
    "TTSRequest",
    "TTSResponse",
    "VOICE_PREFIXES",
    "edge_tts",
    "router",
]

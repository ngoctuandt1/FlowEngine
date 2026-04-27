"""LLM-backed prompt helper endpoints."""

from __future__ import annotations

import json
import os
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints, TypeAdapter

from server import config
import server.llm as llm_client


router = APIRouter(prefix="/api/llm", tags=["llm"])

TextField = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
]


class AutoPromptRequest(BaseModel):
    topic: TextField
    style: TextField = "cinematic"


class ExpandPromptRequest(BaseModel):
    idea: TextField


class ShotListRequest(BaseModel):
    scene: TextField
    n_shots: int = Field(default=5, ge=1, le=12)


class PromptResponse(BaseModel):
    prompt: str


class Shot(BaseModel):
    shot_n: int
    description: str
    duration_seconds: float


class ShotListResponse(BaseModel):
    shots: list[Shot]


SHOT_LIST_ADAPTER = TypeAdapter(list[Shot])

try:
    from anthropic import AuthenticationError as AnthropicAuthenticationError
except ImportError:  # pragma: no cover - optional dependency in tests
    AnthropicAuthenticationError = ()


def _raise_if_llm_unavailable() -> None:
    if config.LLM_DISABLED:
        raise HTTPException(
            status_code=503,
            detail="LLM helpers are disabled. Set LLM_DISABLED=false to enable them.",
        )
    if not (os.getenv("ANTHROPIC_API_KEY") or "").strip():
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY is not set. Configure it to enable LLM helpers.",
        )


async def _call_claude_or_503(*, system: str, user: str, max_tokens: int) -> str:
    _raise_if_llm_unavailable()
    try:
        return await llm_client.call_claude(system=system, user=user, max_tokens=max_tokens)
    except AnthropicAuthenticationError as exc:
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY is invalid or unauthorized. Configure a valid key to enable LLM helpers.",
        ) from exc


@router.post("/auto-prompt", response_model=PromptResponse)
async def auto_prompt(body: AutoPromptRequest) -> PromptResponse:
    prompt = await _call_claude_or_503(
        system=(
            "you generate concise (<60 words) Veo-3 video prompts. "
            f"style: {body.style}. respond with the prompt only."
        ),
        user=body.topic,
        max_tokens=256,
    )
    return PromptResponse(prompt=prompt)


@router.post("/expand-prompt", response_model=PromptResponse)
async def expand_prompt(body: ExpandPromptRequest) -> PromptResponse:
    prompt = await _call_claude_or_503(
        system=(
            "expand the user idea into a detailed cinematic Veo prompt with "
            "camera, lighting, motion, mood. <80 words."
        ),
        user=body.idea,
        max_tokens=384,
    )
    return PromptResponse(prompt=prompt)


@router.post("/shot-list", response_model=ShotListResponse)
async def shot_list(body: ShotListRequest) -> ShotListResponse:
    raw = await _call_claude_or_503(
        system=(
            "Return JSON only. Build a cinematic Veo shot list as a JSON array. "
            "Each item must contain shot_n, description, duration_seconds. "
            f"Return exactly {body.n_shots} shots."
        ),
        user=body.scene,
        max_tokens=768,
    )
    try:
        shots = SHOT_LIST_ADAPTER.validate_python(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Invalid shot-list response from Claude.") from exc

    if len(shots) != body.n_shots:
        raise HTTPException(status_code=502, detail="Claude returned the wrong number of shots.")
    return ShotListResponse(shots=shots)

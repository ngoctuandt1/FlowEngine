"""Pydantic models for the IdeaStudio Gemini planning endpoint."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints


PromptText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]

ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]

RefImageUrl = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
]


class IdeaGenerateRequest(BaseModel):
    prompt: PromptText
    ref_image_urls: list[RefImageUrl] | None = Field(default=None, max_length=5)
    chain_id: str | None = Field(default=None, min_length=1, max_length=128)


class IdeaNode(BaseModel):
    type: ShortText
    prompt: PromptText
    ratio: ShortText
    parent_index: int | None = Field(default=None, ge=0)


class IdeaGenerateResponse(BaseModel):
    script: PromptText
    nodes: list[IdeaNode] = Field(default_factory=list)

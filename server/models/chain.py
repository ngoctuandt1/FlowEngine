"""Chain data models for FlowEngine (B4).

Two shapes:
- `Chain` — immutable metadata row persisted in the `chains` table.
- `ChainAggregate` — `GET /api/chains/{id}` response; status + progress are
  derived on-demand from `jobs` GROUP BY to avoid drift with a synced column.
"""

import uuid
from datetime import UTC, datetime
from typing import Optional

from pydantic import BaseModel, Field

from server.models.job import Job


class Chain(BaseModel):
    """Immutable metadata row in the `chains` table.

    Only fields that never change after INSERT are stored here. Status and
    progress are computed from the `jobs` table on every read — see
    `server/db/chain_store.py::compute_aggregated_status`.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    profile: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChainProgress(BaseModel):
    completed: int
    total: int


class ChainAggregate(BaseModel):
    """`GET /api/chains/{id}` response.

    `status` and `progress` are derived from `SELECT chain_id, status FROM jobs
    GROUP BY chain_id` — never written back to the chains row.
    """
    id: str
    profile: Optional[str] = None
    created_at: datetime
    status: str
    progress: ChainProgress
    jobs: list[str]


class ChainCreateResponse(BaseModel):
    """Chain creation response returned by POST /api/chains and template instantiate."""

    chain_id: str
    jobs: list[Job]

"""Route module exports."""

from server.routes.jobs import router as jobs_router
from server.routes.prompt_builder import router as prompt_builder_router
from server.routes.media_cut import router as media_cut_router
from server.routes.media_merge import router as media_merge_router
from server.routes.media_fetch import router as media_fetch_router
from server.routes.characters import router as characters_router
from server.routes.llm import router as llm_router
from server.routes.uploads import router as uploads_router
from server.routes.worker import router as worker_router
from server.routes.profiles import router as profiles_router
from server.routes.tts import router as tts_router
from server.routes.templates import router as templates_router
from server.routes.ws import router as ws_router

__all__ = [
    "jobs_router",
    "prompt_builder_router",
    "media_cut_router",
    "media_merge_router",
    "media_fetch_router",
    "characters_router",
    "llm_router",
    "uploads_router",
    "worker_router",
    "profiles_router",
    "tts_router",
    "templates_router",
    "ws_router",
]

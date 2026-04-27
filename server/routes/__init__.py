"""Route module exports."""

from server.routes.jobs import router as jobs_router
from server.routes.uploads import router as uploads_router
from server.routes.worker import router as worker_router
from server.routes.profiles import router as profiles_router
from server.routes.templates import router as templates_router
from server.routes.ws import router as ws_router

__all__ = ["jobs_router", "uploads_router", "worker_router", "profiles_router", "templates_router", "ws_router"]

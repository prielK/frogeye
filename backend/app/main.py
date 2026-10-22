from fastapi import APIRouter, FastAPI

from app import __version__
from app.api.v1 import (
    agent_runs,
    auth,
    briefings,
    compare,
    competitors,
    costs,
    demo_snapshot,
    documents,
    evals,
    events,
    health,
    products,
    reference,
    runs,
    search,
    signals,
    sources,
)
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.security import warn_if_weak_secret


def create_app() -> FastAPI:
    configure_logging()
    warn_if_weak_secret(get_settings().jwt_secret)
    app = FastAPI(title="FrogEye", version=__version__)
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(health.router)
    api_v1.include_router(auth.router)
    api_v1.include_router(runs.router)
    api_v1.include_router(competitors.router)
    api_v1.include_router(products.router)
    api_v1.include_router(compare.router)
    api_v1.include_router(reference.router)
    api_v1.include_router(sources.router)
    api_v1.include_router(documents.router)
    api_v1.include_router(search.router)
    api_v1.include_router(briefings.router)
    api_v1.include_router(signals.router)
    api_v1.include_router(agent_runs.router)
    api_v1.include_router(costs.router)
    api_v1.include_router(demo_snapshot.router)
    api_v1.include_router(evals.router)
    api_v1.include_router(events.router)
    app.include_router(api_v1)
    return app


app = create_app()

"""FastAPI entrypoint for Stage 10 minimal HTTP API."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.common.logging import configure_logging, get_logger
from app.common.settings import get_settings

from .routes import router

configure_logging()
logger = get_logger(__name__)


def create_app() -> FastAPI:
    """Build FastAPI application with shared lifecycle hooks."""

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        settings = get_settings()
        logger.info(
            "API service starting",
            extra={
                "project_name": settings.PROJECT_NAME,
                "database_url": settings.masked_database_url,
            },
        )
        yield
        logger.info("API service stopping")

    app = FastAPI(
        title="Proxy Aggregation API",
        version="10.0.0",
        description="Read-only Stage 10 API over proxy_state and output exports.",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


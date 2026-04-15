from contextlib import asynccontextmanager
from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    from app.db import init_db
    from app.services.qdrant import ensure_collection_exists
    from app.workers.scheduler import start_scheduler

    await init_db()
    await ensure_collection_exists()
    start_scheduler()

    yield

    # Shutdown (nothing to clean up for now)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Contacts Catch-Up Voice Assistant",
        description="Proactive outbound voice calls to keep relationships warm.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Register routers (imported lazily to avoid circular imports)
    from app.routes import contacts, calls, webhook, dashboard

    app.include_router(contacts.router, prefix="/api/contacts", tags=["contacts"])
    app.include_router(calls.router, prefix="/api/calls", tags=["calls"])
    app.include_router(webhook.router, tags=["webhook"])
    app.include_router(dashboard.router, tags=["dashboard"])

    @app.get("/health", tags=["health"])
    async def health():
        return {"status": "ok"}

    return app


app = create_app()

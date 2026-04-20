import logging
import logging.config
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# Configure logging before any loggers are created.
# uvicorn's --log-level only affects uvicorn's own loggers; this covers app loggers.
logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "default"},
    },
    "root": {"level": "INFO", "handlers": ["console"]},
    "loggers": {
        "app": {"level": "INFO", "propagate": True},
        "uvicorn": {"level": "INFO", "propagate": False, "handlers": ["console"]},
        "uvicorn.access": {"level": "INFO", "propagate": False, "handlers": ["console"]},
    },
})


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    from app.db import init_db
    from app.services.qdrant import ensure_collection_exists
    from app.workers.scheduler import start_scheduler
    from app.services.vapi import ensure_assistant_server_url
    from app.config import get_settings

    await init_db()
    await ensure_collection_exists()
    start_scheduler()

    # Ensure Vapi assistant has serverUrl configured for end-of-call webhooks
    try:
        settings = get_settings()
        await ensure_assistant_server_url(
            settings.VAPI_API_KEY,
            settings.VAPI_ASSISTANT_ID,
            settings.APP_BASE,
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Could not patch Vapi assistant serverUrl: %s", exc)

    yield

    # Shutdown (nothing to clean up for now)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Contacts Catch-Up Voice Assistant",
        description="Proactive outbound voice calls to keep relationships warm.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Auth middleware — protects /api/* routes when APP_SECRET_KEY is set.
    # Exempt: /webhook/vapi (called by Vapi), /health, /static/*, / (HTML login handles itself)
    # SSE endpoints accept token as ?token= query param (EventSource can't set headers).
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        from app.config import get_settings
        try:
            secret = get_settings().APP_SECRET_KEY
        except Exception:
            return await call_next(request)

        if not secret:
            return await call_next(request)

        path = request.url.path
        # Open paths — no auth required
        if not path.startswith("/api/") or path.startswith("/webhook/"):
            return await call_next(request)

        # Check Authorization: Bearer <token> header
        auth_header = request.headers.get("Authorization", "")
        if auth_header == f"Bearer {secret}":
            return await call_next(request)

        # SSE fallback: ?token=<secret> query param (EventSource doesn't support headers)
        if request.query_params.get("token") == secret:
            return await call_next(request)

        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    # Register routers (imported lazily to avoid circular imports)
    from app.routes import contacts, calls, webhook, dashboard

    app.include_router(contacts.router, prefix="/api/contacts", tags=["contacts"])
    app.include_router(calls.router, prefix="/api/calls", tags=["calls"])
    app.include_router(webhook.router, tags=["webhook"])
    app.include_router(dashboard.router, tags=["dashboard"])

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    @app.get("/health", tags=["health"])
    async def health():
        return {"status": "ok"}

    return app


app = create_app()

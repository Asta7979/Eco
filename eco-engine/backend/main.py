"""
FastAPI application for Project Eco (EcoRetrofit AI Engine).

Layout follows fastapi/full-stack-fastapi-template:
    core/config.py      typed settings
    api/deps.py         SessionDep / CurrentUser dependencies
    api/routes/*.py     one router per domain, collected in api/main.py
    services/           shared assessment logic + the portfolio cache
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.api.main import api_router
from backend.auth import validate_auth_configuration
from backend.core.config import settings
from backend.core.security import BodySizeLimitMiddleware, RateLimitMiddleware, SecurityHeadersMiddleware
from backend.db import database
from backend.engine import vision_engine

@asynccontextmanager
async def lifespan(app: FastAPI):
    # No demo data is loaded: every home is added by its signed-in owner.
    validate_auth_configuration()
    from backend.db.migrate import ensure_schema

    ensure_schema()  # Alembic: migrate fresh / managed databases, adopt legacy ones
    # Load the open-vocabulary detector in the background (photo assistance).
    from backend.services import remote

    remote.vision_warm_up()  # no-op when the vision microservice is configured
    yield


class CachedStaticFiles(StaticFiles):
    """Static files with browser caching: images/fonts for a week, HTML/CSS/JS for an hour.
    ETag / Last-Modified revalidation is kept, so edited files are picked up."""

    ASSET_EXT = (".jpg", ".jpeg", ".png", ".webp", ".svg", ".woff2", ".ico")

    def file_response(self, full_path, *args, **kwargs):
        resp = super().file_response(full_path, *args, **kwargs)
        path = str(full_path).lower()
        if path.endswith(".html"):
            resp.headers["Cache-Control"] = "no-cache"
        elif path.endswith(self.ASSET_EXT):
            resp.headers["Cache-Control"] = f"public, max-age={settings.ASSET_MAX_AGE_S}"
        else:
            resp.headers["Cache-Control"] = f"public, max-age={settings.STATIC_MAX_AGE_S}, must-revalidate"
        return resp


logger = logging.getLogger("eco")

app = FastAPI(
    title=settings.PROJECT_NAME,
    description=settings.DESCRIPTION,
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

# Middleware runs outermost-last-added: security headers wrap everything (so even 413/429
# responses carry them), then rate limiting, then the body-size cap, then compression.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens, not cookies: no credentialed CORS needed
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )
app.add_middleware(GZipMiddleware, minimum_size=settings.GZIP_MIN_BYTES)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    """Never leak stack traces or internals to clients; log them server-side."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Something went wrong. Please try again."})

app.include_router(api_router)

if settings.web_dir.exists():
    app.mount("/portal", CachedStaticFiles(directory=str(settings.web_dir), html=True), name="portal")


@app.get("/", include_in_schema=False)
def root_redirect():
    """Redirect root to the web landing page."""
    return RedirectResponse(url="/portal/")


if __name__ == "__main__":
    import os

    import uvicorn

    # Development convenience only: auto-reload and loopback binding unless production.
    production = settings.is_production
    uvicorn.run(
        "backend.main:app",
        host=os.getenv("ECO_HOST", "0.0.0.0" if production else "127.0.0.1"),
        port=int(os.getenv("ECO_PORT", "8000")),
        reload=not production,
        proxy_headers=os.getenv("ECO_TRUST_PROXY_HEADERS", "0") == "1",
        server_header=False,
    )

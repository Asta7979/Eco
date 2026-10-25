"""
Shared scaffolding for the internal microservices (vision, LLM, physics).

* Internal-only: every endpoint except /health requires the X-Service-Token header to match
  ECO_SERVICE_TOKEN (compared in constant time). In production the token is mandatory.
* No API docs in production, no Server banner, generic 500 bodies, security headers and the
  same request-size limit as the gateway.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from backend.core.security import BodySizeLimitMiddleware, SecurityHeadersMiddleware

logger = logging.getLogger("eco.service")


def _production() -> bool:
    return os.getenv("ENVIRONMENT", "").lower() == "production"


def require_service_token(x_service_token: Optional[str] = Header(None)) -> None:
    expected = os.getenv("ECO_SERVICE_TOKEN", "")
    if not expected:
        if _production():
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Service token not configured.")
        return  # local development: services are reached in-process or on localhost
    if not x_service_token or not hmac.compare_digest(x_service_token, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid service token.")


ServiceAuth = Depends(require_service_token)


def create_service_app(name: str, description: str, upload_paths: tuple = (), version: str = "1.0.0", **kwargs) -> FastAPI:
    if _production() and len(os.getenv("ECO_SERVICE_TOKEN", "")) < 32:
        raise RuntimeError("ECO_SERVICE_TOKEN must be at least 32 characters in production")
    docs = not _production()
    app = FastAPI(
        title=name,
        description=description,
        version=version,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
        **kwargs,
    )
    app.add_middleware(BodySizeLimitMiddleware, upload_paths=upload_paths)  # photo routes get the larger limit
    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("Unhandled error in %s on %s", name, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal service error."})

    @app.get("/health", tags=["System"])
    def health():
        return {"status": "healthy", "service": name}

    return app

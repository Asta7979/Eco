"""
HTTP hardening for Project Eco: security headers, rate limiting and request-size limits.

Implemented as pure ASGI middleware (no extra dependencies):

* SecurityHeadersMiddleware  CSP (built per request so the Supabase origin is only allowed
                             when Supabase Auth is on), frame/sniffing/referrer/permissions
                             headers, cross-origin isolation, and HSTS on HTTPS.
* RateLimitMiddleware        sliding-window limits per client IP, tighter on sign-in,
                             registration, the vision models and the advisor.
* BodySizeLimitMiddleware    rejects oversized requests before they are read.

Client IP: the socket peer by default. Behind a reverse proxy set ECO_TRUST_PROXY_HEADERS=1
so the left-most X-Forwarded-For address is used (only do this when the proxy overwrites
that header, otherwise clients could spoof it).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

from backend.auth import supabase_settings

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def is_production() -> bool:
    return os.getenv("ENVIRONMENT", "").lower() == "production"


def trust_proxy_headers() -> bool:
    return os.getenv("ECO_TRUST_PROXY_HEADERS", "0") == "1"


def client_ip(scope) -> str:
    if trust_proxy_headers():
        for name, value in scope.get("headers", []):
            if name == b"x-forwarded-for":
                first = value.decode("latin-1").split(",")[0].strip()
                if first:
                    return first
    client = scope.get("client")
    return client[0] if client else "unknown"


async def _send_json(send, status: int, detail: str, extra_headers: Optional[List[Tuple[bytes, bytes]]] = None):
    body = json.dumps({"detail": detail}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    headers += extra_headers or []
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

def content_security_policy() -> str:
    supabase_url, _ = supabase_settings()
    script_src = ["'self'"]
    connect_src = ["'self'"]
    if supabase_url:
        # supabase-js is loaded as an ES module from jsDelivr, and talks to the project URL.
        script_src.append("https://cdn.jsdelivr.net")
        connect_src.append(supabase_url)
    directives = {
        "default-src": ["'self'"],
        "script-src": script_src,
        # Inline style attributes are used for layout values; scripts stay strict.
        "style-src": ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
        "font-src": ["'self'", "https://fonts.gstatic.com"],
        "img-src": ["'self'", "data:", "blob:"],
        "media-src": ["'self'", "blob:"],
        "connect-src": connect_src,
        "object-src": ["'none'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
        "frame-ancestors": ["'none'"],
        "worker-src": ["'none'"],
    }
    if is_production():
        directives["upgrade-insecure-requests"] = []
    return "; ".join(f"{k} {' '.join(v)}".strip() for k, v in directives.items())


# Swagger UI / ReDoc load their own assets from jsDelivr with inline bootstrapping, so they
# get a separate, narrower policy (and are disabled in production unless explicitly enabled).
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
    "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
)
DOCS_PATHS = ("/docs", "/redoc")


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        https = scope.get("scheme") == "https" or (
            trust_proxy_headers()
            and any(n == b"x-forwarded-proto" and v.split(b",")[0].strip() == b"https" for n, v in scope.get("headers", []))
        )

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in {b"server"}]
                csp = DOCS_CSP if path.startswith(DOCS_PATHS) else content_security_policy()
                headers += [
                    (b"content-security-policy", csp.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"permissions-policy", b"camera=(self), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                    (b"cross-origin-resource-policy", b"same-origin"),
                    (b"x-permitted-cross-domain-policies", b"none"),
                ]
                if https:
                    headers.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains"))
                if path.startswith(("/auth", "/properties", "/scenarios", "/audit", "/admin", "/evaluate", "/scenario", "/api/v1")):
                    headers.append((b"cache-control", b"no-store"))  # personal data never cached
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class SlidingWindowLimiter:
    """At most `limit` hits per key in any rolling `window_s`. Thread-safe, memory-capped."""

    MAX_KEYS = 50_000

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window_s = limit, window_s
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> Optional[float]:
        """Records a hit; returns None if allowed, else seconds until the next slot frees."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                if len(self._hits) >= self.MAX_KEYS:
                    self._evict(now)
                q = self._hits[key] = deque()
            while q and now - q[0] >= self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return max(1.0, self.window_s - (now - q[0]))
            q.append(now)
            return None

    def _evict(self, now: float) -> None:
        stale = [k for k, q in self._hits.items() if not q or now - q[-1] >= self.window_s]
        for k in stale:
            del self._hits[k]
        if len(self._hits) >= self.MAX_KEYS:  # still full: drop the oldest half
            for k in list(self._hits)[: self.MAX_KEYS // 2]:
                del self._hits[k]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def _limit(env: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(env, default)))
    except ValueError:
        return default


# (method, path prefix) -> limiter. First match wins; "*" matches any method.
RATE_RULES: List[Tuple[str, str, SlidingWindowLimiter]] = [
    ("POST", "/auth/login", SlidingWindowLimiter(_limit("ECO_RATE_LOGIN_PER_MIN", 10), 60)),
    ("POST", "/auth/register", SlidingWindowLimiter(_limit("ECO_RATE_REGISTER_PER_HOUR", 10), 3600)),
    ("POST", "/auth/password", SlidingWindowLimiter(_limit("ECO_RATE_LOGIN_PER_MIN", 10), 60)),
    ("POST", "/vision/segment", SlidingWindowLimiter(_limit("ECO_RATE_SEGMENT_PER_MIN", 6), 60)),
    ("POST", "/vision/analyze", SlidingWindowLimiter(_limit("ECO_RATE_VISION_PER_MIN", 20), 60)),
    # Live camera: about one frame a second per viewer, on the fast path.
    ("POST", "/vision/live", SlidingWindowLimiter(_limit("ECO_RATE_LIVE_PER_MIN", 150), 60)),
    ("POST", "/api/v1/cv", SlidingWindowLimiter(_limit("ECO_RATE_VISION_PER_MIN", 20), 60)),
    ("POST", "/scenario/chat", SlidingWindowLimiter(_limit("ECO_RATE_CHAT_PER_MIN", 30), 60)),
    ("POST", "/api/v1/scenario", SlidingWindowLimiter(_limit("ECO_RATE_CHAT_PER_MIN", 30), 60)),
    ("POST", "/api/v1/physics", SlidingWindowLimiter(_limit("ECO_RATE_SEGMENT_PER_MIN", 6), 60)),
    ("*", "/", SlidingWindowLimiter(_limit("ECO_RATE_API_PER_MIN", 600), 60)),
]
STATIC_PREFIXES = ("/portal/",)


def reset_rate_limits() -> None:
    for _, _, limiter in RATE_RULES:
        limiter.reset()


class RateLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or os.getenv("ECO_RATE_LIMIT", "1") == "0":
            return await self.app(scope, receive, send)
        path, method = scope.get("path", ""), scope.get("method", "GET")
        if path.startswith(STATIC_PREFIXES) or method == "OPTIONS":
            return await self.app(scope, receive, send)
        ip = client_ip(scope)
        for rule_method, prefix, limiter in RATE_RULES:
            if (rule_method in ("*", method)) and path.startswith(prefix):
                retry = limiter.hit(f"{prefix}|{ip}")
                if retry is not None:
                    return await _send_json(
                        send, 429, "Too many requests. Please wait and try again.",
                        [(b"retry-after", str(int(retry)).encode())],
                    )
                break
        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Request size
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = 13 * 1024 * 1024   # photo uploads (the vision engine itself caps at 12 MB)
MAX_JSON_BYTES = 256 * 1024           # every other request body


# Paths that take photo uploads (gateway defaults); the vision service passes its own.
GATEWAY_UPLOAD_PATHS = ("/vision/", "/api/v1/cv")


class BodySizeLimitMiddleware:
    def __init__(self, app, upload_paths=GATEWAY_UPLOAD_PATHS):
        self.app = app
        self.upload_paths = tuple(upload_paths)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        limit = MAX_UPLOAD_BYTES if path.startswith(self.upload_paths) else MAX_JSON_BYTES
        declared = next((v for n, v in scope.get("headers", []) if n == b"content-length"), None)
        if declared is not None:
            try:
                if int(declared) > limit:
                    return await _send_json(send, 413, "Request is too large.")
            except ValueError:
                return await _send_json(send, 400, "Invalid Content-Length.")

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _TooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _TooLarge:
            await _send_json(send, 413, "Request is too large.")


class _TooLarge(Exception):
    pass

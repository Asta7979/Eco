"""
Gateway -> microservice clients.

Each capability has two modes:
* remote     the service URL env var is set (Docker Compose): call that service over HTTP
             with the shared service token;
* in-process the URL is unset (local development, tests): run the same library code here.

Services:
    VISION_SERVICE_URL   services/vision_service   POST /detect, /segment, GET /status
    LLM_SERVICE_URL      services/llm_service      POST /explain
    PHYSICS_SERVICE_URL  services/physics_engine   POST /simulate, /simulate/detailed
    ECO_SERVICE_TOKEN    shared secret sent as X-Service-Token on every internal call

The gateway stays zero-trust towards the LLM service: numbers in a reply are re-checked
against the audited results here, so even a faulty service cannot inject unverified figures.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Tests inject a client per service (e.g. a TestClient wrapping the service app).
_clients: Dict[str, httpx.Client] = {}


def _url(env: str) -> Optional[str]:
    v = (os.getenv(env) or "").strip().rstrip("/")
    return v or None


def _headers() -> Dict[str, str]:
    token = os.getenv("ECO_SERVICE_TOKEN")
    return {"X-Service-Token": token} if token else {}


_pool: Dict[str, httpx.Client] = {}


def _client(env: str, timeout: float) -> httpx.Client:
    if env in _clients:
        return _clients[env]
    url = _url(env)
    pooled = _pool.get(env)
    if pooled is None or str(pooled.base_url).rstrip("/") != url:
        pooled = _pool[env] = httpx.Client(base_url=url, timeout=timeout)  # keep-alive connection pool
    return pooled


def set_test_client(env: str, client: Optional[httpx.Client]) -> None:
    if client is None:
        _clients.pop(env, None)
    else:
        _clients[env] = client


def _raise_for(resp: httpx.Response, service: str) -> None:
    if resp.status_code < 400:
        return
    try:
        detail = resp.json().get("detail")
    except Exception:
        detail = None
    if resp.status_code in (400, 413, 422) and isinstance(detail, str):
        raise HTTPException(resp.status_code, detail)  # the user's input problem: pass it through
    logger.warning("%s service returned %s", service, resp.status_code)
    raise HTTPException(502, f"The {service} service is unavailable. Please try again.")


def _call(env: str, service: str, method: str, path: str, timeout: float, **kw) -> Any:
    try:
        with_client = _client(env, timeout)
        resp = with_client.request(method, path, headers=_headers(), timeout=timeout, **kw)
    except httpx.HTTPError as exc:
        logger.warning("%s service unreachable: %s", service, exc)
        raise HTTPException(503, f"The {service} service is unreachable. Please try again.")
    _raise_for(resp, service)
    return resp.json()


def remote_enabled(env: str) -> bool:
    return env in _clients or _url(env) is not None


# ---------------------------------------------------------------------------
# Vision
# ---------------------------------------------------------------------------

def vision_analyze(data: bytes, filename: str, content_type: str, mode: str, live: bool = False) -> Dict:
    if remote_enabled("VISION_SERVICE_URL"):
        return _call("VISION_SERVICE_URL", "vision", "POST", "/detect", 15.0 if live else 60.0,
                     files={"file": (filename or "photo", data, content_type or "application/octet-stream")},
                     data={"mode": mode, "live": "true" if live else "false"})
    from backend.engine import vision_engine

    return vision_engine.analyze_image(data, mode, live)


def vision_segment(data: bytes, filename: str, content_type: str, boxes: List[List[float]]) -> Dict:
    if remote_enabled("VISION_SERVICE_URL"):
        return _call("VISION_SERVICE_URL", "vision", "POST", "/segment", 120.0,
                     files={"file": (filename or "photo", data, content_type or "application/octet-stream")},
                     data={"boxes": json.dumps(boxes)})
    from backend.engine import vision_engine

    return vision_engine.segment_image(data, boxes)


def vision_status() -> Dict:
    if remote_enabled("VISION_SERVICE_URL"):
        return _call("VISION_SERVICE_URL", "vision", "GET", "/status", 10.0)
    from backend.engine import vision_engine

    return vision_engine.vision_status()


def vision_warm_up() -> None:
    if not remote_enabled("VISION_SERVICE_URL"):
        from backend.engine import vision_engine

        vision_engine.warm_up_async()


# ---------------------------------------------------------------------------
# LLM explanation (zero math, zero trust)
# ---------------------------------------------------------------------------

def explain(metrics: Any, user_query: str, template_text: str) -> Tuple[str, str, bool]:
    """Same contract as llm_explainer.explain: (text, source, numeric_grounding_passed)."""
    from backend.engine import llm_explainer

    if not remote_enabled("LLM_SERVICE_URL"):
        return llm_explainer.explain(metrics, user_query, template_text)

    payload = metrics.model_dump(mode="json") if hasattr(metrics, "model_dump") else metrics
    timeout = llm_explainer.timeout_s() + 4.0
    try:
        out = _call("LLM_SERVICE_URL", "llm", "POST", "/explain", timeout,
                    json={"metrics": payload, "user_query": user_query, "template_text": template_text})
    except HTTPException:
        return template_text, "template", True  # the advisor always answers

    text, source = str(out.get("text") or ""), out.get("source")
    if source != "llm" or not text:
        return template_text, "template", bool(out.get("numeric_grounding_passed", True))
    # Re-validate here: never trust another service with the "no invented numbers" rule.
    allowed = llm_explainer.allowed_numbers(payload, user_query, list(range(0, 11)), 100)
    if llm_explainer.ungrounded_numbers(text, allowed):
        logger.warning("LLM service reply failed gateway-side grounding; using template")
        return template_text, "template", False
    return text, "llm", True


def parse_intent(orchestrator, user_query: str, default_property_id: str):
    """What-if question -> ScenarioIntent, in the LLM service when configured (deterministic either way)."""
    from backend.schemas import ScenarioIntent

    if not remote_enabled("LLM_SERVICE_URL"):
        return orchestrator.extract_intent(user_query, default_property_id)
    try:
        out = _call("LLM_SERVICE_URL", "llm", "POST", "/parse", 10.0,
                    json={"user_query": user_query, "default_property_id": default_property_id})
        return ScenarioIntent(**out)
    except HTTPException:
        return orchestrator.extract_intent(user_query, default_property_id)  # same rules, local


# ---------------------------------------------------------------------------
# Physics
# ---------------------------------------------------------------------------

def physics_detailed(prop: Dict, measures: List[str]) -> Dict:
    """ISO 52016 hourly simulation (pyBuildingEnergy). Runs only in the physics service."""
    if not remote_enabled("PHYSICS_SERVICE_URL"):
        raise HTTPException(
            503,
            "Detailed ISO 52016 simulation runs in the physics service. Start it with Docker Compose.",
        )
    return _call("PHYSICS_SERVICE_URL", "physics", "POST", "/simulate/detailed", 180.0,
                 json={"property": prop, "measures": measures})

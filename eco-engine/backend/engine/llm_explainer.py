"""
Guarded LLM Explainer (Project Eco).

The model only rewords audited engine output into plain English. It never computes.
Every number in its reply is checked against the numbers the deterministic
engines produced (100% numeric grounding). Any reply that fails the check, times
out, is refused or errors falls back to the deterministic template, so the
endpoint always returns a valid, grounded explanation.

Opt-in: the LLM path runs only when a provider is configured. Otherwise the template is used.

Providers (ECO_LLM_PROVIDER, or picked automatically from the first credential found):
    anthropic  ANTHROPIC_API_KEY (or ECO_LLM_ENABLED=1 with another Anthropic credential source)
    gemini     GEMINI_API_KEY (or GOOGLE_API_KEY), Google Gemini API
    ollama     OLLAMA_HOST, a local Ollama server (e.g. Gemma)
    none       always use the template

Environment:
    ECO_LLM_MODEL      default per provider (see DEFAULT_MODELS)
    ECO_LLM_TIMEOUT_S  default 8 seconds, 60 for Ollama (no retries; on timeout the template is used)
"""

import json
import logging
import os
import re
from typing import Any, Iterable, Optional, Set, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5-5",
    "gemini": "gemini-2.5-flash",
    "ollama": "gemma4",
}
DEFAULT_MODEL = DEFAULT_MODELS["anthropic"]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = """You explain home-retrofit results to UK homeowners and mortgage advisors.

You receive a JSON object of results already computed by audited calculation engines.
Rules:
- Do not calculate, estimate, round differently, convert units, or add up anything.
- Only quote numbers exactly as they appear in the JSON (thousands separators are fine).
- Never introduce a number that is not in the JSON, including percentages, years or prices.
- Explain what the user asked about first, then the most useful supporting facts.
- If fabric measures were added as prerequisites, say why in one sentence (fabric-first).
- These are indicative estimates, not a valuation or regulated mortgage advice.
- Use short Markdown bullet points with **bold** key figures. At most 170 words."""

_NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?")


def provider() -> Optional[str]:
    """The configured LLM provider, or None when only the template should be used."""
    chosen = os.getenv("ECO_LLM_PROVIDER", "").strip().lower()
    if chosen in DEFAULT_MODELS:
        return chosen
    if chosen not in ("", "auto"):
        return None
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if _gemini_key():
        return "gemini"
    if os.getenv("OLLAMA_HOST"):
        return "ollama"
    if os.getenv("ECO_LLM_ENABLED") == "1":
        return "anthropic"
    return None


def llm_enabled() -> bool:
    return provider() is not None


def _gemini_key() -> Optional[str]:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None


def _model(name: str) -> str:
    return os.getenv("ECO_LLM_MODEL") or DEFAULT_MODELS[name]


def timeout_s() -> float:
    """Per-request LLM timeout. Local Ollama models are slower, so they get a longer default."""
    return float(os.getenv("ECO_LLM_TIMEOUT_S") or (60 if provider() == "ollama" else 8))


def _ask_anthropic(prompt: str) -> Optional[str]:
    import anthropic

    client = anthropic.Anthropic().with_options(timeout=timeout_s(), max_retries=0)
    response = client.beta.messages.create(
        model=_model("anthropic"),
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        return None
    return "".join(b.text for b in response.content if b.type == "text")


def _ask_gemini(prompt: str) -> Optional[str]:
    import httpx

    key = _gemini_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    r = httpx.post(
        GEMINI_URL.format(model=_model("gemini")),
        headers={"x-goog-api-key": key},  # header, not ?key=, so it never lands in URL logs
        json={
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 8192},
        },
        timeout=timeout_s(),
    )
    r.raise_for_status()
    data = r.json()
    if data.get("promptFeedback", {}).get("blockReason"):
        return None
    candidates = data.get("candidates") or []
    if not candidates or candidates[0].get("finishReason") in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT"):
        return None
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _ask_ollama(prompt: str) -> Optional[str]:
    import httpx

    host = (os.getenv("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
    if "://" not in host:
        host = f"http://{host}"
    r = httpx.post(
        f"{host}/api/chat",
        json={
            "model": _model("ollama"),
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=timeout_s(),
    )
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "")


_PROVIDERS = {"anthropic": _ask_anthropic, "gemini": _ask_gemini, "ollama": _ask_ollama}


def _flatten_numbers(value: Any) -> Iterable[float]:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _flatten_numbers(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _flatten_numbers(v)
    elif isinstance(value, str):
        for m in _NUMBER_RE.findall(value):
            parsed = _parse_number(m)
            if parsed is not None:
                yield parsed


def _parse_number(token: str):
    try:
        return float(token.replace(",", "").lstrip("+"))
    except ValueError:
        return None


def allowed_numbers(*sources: Any) -> Set[float]:
    """Every number the reply may quote, in the display forms people use for it."""
    allowed: Set[float] = set()
    for n in (n for s in sources for n in _flatten_numbers(s)):
        for v in (n, abs(n)):
            allowed.update({v, round(v), round(v, 1), round(v, 2)})
            # Ratios stored as fractions are often shown as percentages (0.25 -> 25%).
            if 0 < v <= 1:
                allowed.add(round(v * 100, 2))
    return allowed


def ungrounded_numbers(text: str, allowed: Set[float]) -> list:
    """Numbers in `text` that do not match any audited value."""
    bad = []
    for token in _NUMBER_RE.findall(text):
        n = _parse_number(token)
        if n is None:
            continue
        candidates = {n, abs(n), round(abs(n), 1), round(abs(n), 2)}
        if not candidates & allowed:
            bad.append(token)
    return bad


def explain(
    metrics: Any,
    user_query: str,
    template_text: str,
) -> Tuple[str, str, bool]:
    """
    Returns (explanation, source, numeric_grounding_passed).
    source is "llm" or "template".
    """
    name = provider()
    if name is None:
        return template_text, "template", True

    payload = metrics.model_dump() if hasattr(metrics, "model_dump") else metrics
    prompt = (
        f"<question>{user_query}</question>\n"
        f"<audited_results>{json.dumps(payload, default=str)}</audited_results>"
    )

    try:
        text = _PROVIDERS[name](prompt)
    except Exception as exc:  # network, auth, timeout: the template is always safe
        logger.warning("LLM explainer (%s) unavailable, using template: %s", name, exc)
        return template_text, "template", True

    text = (text or "").strip()
    if not text:  # refusal, safety block or empty reply
        return template_text, "template", True

    # Small integers are allowed for list ordering ("Phase 1") and "100%" assurance wording.
    allowed = allowed_numbers(payload, user_query, list(range(0, 11)), 100)
    bad = ungrounded_numbers(text, allowed)
    if bad:
        logger.warning("LLM reply failed numeric grounding %s; using template", bad)
        return template_text, "template", False

    return text, "llm", True

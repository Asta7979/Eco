"""
Guarded LLM microservice: POST /parse, POST /explain.
Holds the only copy of the LLM API key (Anthropic or Gemini) or the Ollama address. Does zero maths: all numbers come from the gateway's
deterministic engines and are validated before any reply leaves this service.
"""

from fastapi import HTTPException

from backend.engine.llm_explainer import llm_enabled, provider
from services.common.app_factory import ServiceAuth, create_service_app
from services.llm_service.app import scenario_parser
from services.llm_service.app.guardrails import ExplainRequest, ExplainResponse, ParsedIntent, ParseRequest

app = create_service_app(
    "Eco guarded LLM service",
    "Parses what-if questions into validated parameters and rewords audited results with zero numeric hallucination.",
)


@app.post("/parse", response_model=ParsedIntent, dependencies=[ServiceAuth])
def parse(req: ParseRequest):
    try:
        return scenario_parser.parse(req.user_query, req.default_property_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/explain", response_model=ExplainResponse, dependencies=[ServiceAuth])
def explain(req: ExplainRequest):
    return scenario_parser.explain(req.metrics, req.user_query, req.template_text)


@app.get("/status", dependencies=[ServiceAuth])
def status():
    return {"llm_enabled": llm_enabled(), "provider": provider() or "template", "fallback": "deterministic template", "numeric_grounding": "enforced"}

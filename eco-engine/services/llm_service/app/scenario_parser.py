"""
Scenario parser + explanation builder for the LLM service.

* parse(): natural-language "what if" -> validated structured parameters. Deterministic keyword
  and budget extraction (the same rules the gateway has always used), so parsing is
  reproducible and never invents a measure.
* explain(): rewords audited engine results. The LLM (Claude, Gemini or a local Ollama model
  such as Gemma, whichever is configured in this container) only rephrases; every number in its reply must
  match the audited results, otherwise the deterministic template is returned. Timeouts,
  refusals and errors also fall back to the template.
"""

from typing import Any, Dict

from backend.engine import llm_explainer
from backend.engine.llm_guardrails import GuardedScenarioOrchestrator
from services.llm_service.app.guardrails import ExplainResponse, ParsedIntent, grounded

_parser = GuardedScenarioOrchestrator(full_retrofit_evaluator=None)


def parse(user_query: str, default_property_id: str) -> ParsedIntent:
    intent = _parser.extract_intent(user_query, default_property_id)
    return ParsedIntent(**intent.model_dump())


def explain(metrics: Dict[str, Any], user_query: str, template_text: str) -> ExplainResponse:
    text, source, passed = llm_explainer.explain(metrics, user_query, template_text)
    # Belt and braces: validate again on the way out of this service.
    if source == "llm" and not grounded(text, metrics, user_query):
        return ExplainResponse(text=template_text, source="template", numeric_grounding_passed=False)
    return ExplainResponse(text=text, source=source, numeric_grounding_passed=passed)

"""
Guardrails for the LLM service (pattern from tleers/llm-api-starterkit: strict Pydantic v2
contracts on every input and output, validation before anything leaves the service).

Rules enforced here:
* Inputs are size-bounded; the audited metrics are opaque JSON produced by the gateway engines.
* Outputs: the explanation may only contain numbers that appear in the audited metrics or the
  question (100 % numeric grounding). Any failure returns the deterministic template instead.
* The parser only emits catalogue action codes and a budget within a sane range; it never
  computes energy, carbon, cost or value.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from backend.engine.dag_engine import RETROFIT_ACTIONS_CATALOGUE
from backend.engine.llm_explainer import allowed_numbers, ungrounded_numbers


class ExplainRequest(BaseModel):
    metrics: Dict[str, Any] = Field(..., description="Audited results from the gateway engines")
    user_query: str = Field(..., min_length=1, max_length=500)
    template_text: str = Field(..., min_length=1, max_length=8000)


class ExplainResponse(BaseModel):
    text: str
    source: Literal["llm", "template"]
    numeric_grounding_passed: bool


class ParseRequest(BaseModel):
    user_query: str = Field(..., min_length=1, max_length=500)
    default_property_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ParsedIntent(BaseModel):
    detected_property_id: str
    requested_interventions: List[str] = Field(..., max_length=20)
    user_primary_goal: Literal["carbon_and_bills", "property_value", "carbon_reduction", "bill_savings"]
    budget_constraint_gbp: Optional[float] = Field(None, ge=1000, le=100_000)

    @field_validator("requested_interventions")
    @classmethod
    def only_catalogue_codes(cls, codes: List[str]) -> List[str]:
        unknown = [c for c in codes if c not in RETROFIT_ACTIONS_CATALOGUE]
        if unknown:
            raise ValueError(f"Unknown action codes: {unknown}")
        return codes


def grounded(text: str, metrics: Dict[str, Any], user_query: str) -> bool:
    """True when every number in the text is an audited number (or a small ordinal / 100 %)."""
    allowed = allowed_numbers(metrics, user_query, list(range(0, 11)), 100)
    return not ungrounded_numbers(text, allowed)

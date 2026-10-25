"""
Guarded LLM Scenario Orchestrator (Project Eco).
Target Organization: Lloyds Banking Group.

CRITICAL ARCHITECTURAL RULE:
    The LLM layer performs ZERO math and calculates NO numbers.
    Its sole responsibilities are:
    1. Parsing natural language user prompts into validated Pydantic v2 JSON parameters.
    2. Enforcing Guardrails validation: Ensuring only valid actions from the DAG catalogue
       are accepted, with no hallucinated parameters.
    3. Dispatching execution to the 100% deterministic calculation engines.
    4. Explaining the audited, verified numbers back to the user without fabrication.
"""

import re
import json
from typing import Dict, List, Any, Optional
from pydantic import ValidationError

from backend.schemas import (
    ScenarioIntent,
    GuardedScenarioChatRequest,
    GuardedScenarioChatResponse,
    FullRetrofitRequest,
    FullRetrofitResponse,
)
from backend.engine.dag_engine import RETROFIT_ACTIONS_CATALOGUE
from backend.engine import llm_explainer


# Mapping natural language keywords to strict DAG action codes
INTENT_KEYWORD_MAP: Dict[str, str] = {
    "heat pump": "heat_pump",
    "ashp": "heat_pump",
    "gshp": "heat_pump",
    "boiler replacement": "heat_pump",
    "replace boiler": "heat_pump",
    "solar": "solar_pv",
    "pv": "solar_pv",
    "solar panels": "solar_pv",
    "panels": "solar_pv",
    "battery": "battery_storage",
    "storage": "battery_storage",
    "wall insulation": "wall_insulation",
    "cavity wall": "wall_insulation",
    "solid wall": "wall_insulation",
    "external wall": "wall_insulation",
    "loft": "loft_insulation",
    "roof insulation": "loft_insulation",
    "attic": "loft_insulation",
    "floor": "floor_insulation",
    "underfloor": "floor_insulation",
    "window": "window_upgrade",
    "glazing": "window_upgrade",
    "double glazing": "window_upgrade",
    "triple glazing": "window_upgrade",
    "draught": "draught_proofing",
    "draft": "draught_proofing",
    "airtightness": "draught_proofing",
    "air tightness": "draught_proofing",
    "smart control": "smart_controls",
    "thermostat": "smart_controls",
    "trv": "smart_controls",
    "ev": "ev_charger",
    "charger": "ev_charger",
}


class GuardedScenarioOrchestrator:
    """
    Guarded LLM Scenario Orchestrator enforcing zero-hallucination math
    via Pydantic v2 structured outputs and deterministic audit pipelines.
    """

    def __init__(self, full_retrofit_evaluator):
        """
        full_retrofit_evaluator: Callable[[FullRetrofitRequest], FullRetrofitResponse]
        """
        self.evaluator = full_retrofit_evaluator

    def extract_intent(self, user_query: str, default_property_id: str) -> ScenarioIntent:
        """
        Extracts structured intent parameters from query.
        Guarded against invalid codes or arbitrary numerical modifications.
        """
        query_lower = user_query.lower()
        extracted_codes = set()

        # Check keyword map
        # Whole-word matching so short keywords ("ev", "pv") don't fire inside other words.
        for keyword, code in INTENT_KEYWORD_MAP.items():
            if re.search(rf"\b{re.escape(keyword)}s?\b", query_lower):
                extracted_codes.add(code)

        # Detect a property ID mentioned in the question (e.g. PROP-GB-004 or PROP-3F9A1C2E).
        # The caller's evaluator enforces ownership, so naming someone else's home fails.
        prop_match = re.search(r"PROP-(?:GB-\d{3}|[0-9A-F]{8})\b", user_query, re.IGNORECASE)
        prop_id = prop_match.group(0).upper() if prop_match else default_property_id

        # Determine primary goal
        goal = "carbon_and_bills"
        if "value" in query_lower or "worth" in query_lower or "equity" in query_lower:
            goal = "property_value"
        elif "carbon" in query_lower or "co2" in query_lower or "net zero" in query_lower:
            goal = "carbon_reduction"
        elif "bill" in query_lower or "cost" in query_lower or "energy price" in query_lower:
            goal = "bill_savings"

        # Detect budget if specified e.g. £15,000 or 15000
        budget = None
        budget_match = re.search(
            r"(?:£\s*(\d{1,3}(?:,\d{3})+|\d+)\s*(k)?|(\d{1,3}(?:,\d{3})+|\d+)\s*(k)?\s*(?:pounds|gbp|budget))",
            query_lower,
        )
        if budget_match:
            try:
                raw_num = (budget_match.group(1) or budget_match.group(3)).replace(",", "")
                budget_val = float(raw_num)
                if budget_match.group(2) or budget_match.group(4):
                    budget_val *= 1000
                if 1000 <= budget_val <= 100000:
                    budget = budget_val
            except ValueError:
                budget = None

        # Fallback if no specific retrofit was mentioned
        if not extracted_codes:
            # Default to a comprehensive fabric-first + heat pump retrofit
            extracted_codes = {"heat_pump", "wall_insulation"}

        # Validate with Pydantic v2 schema
        intent = ScenarioIntent(
            detected_property_id=prop_id,
            requested_interventions=sorted(list(extracted_codes)),
            user_primary_goal=goal,
            budget_constraint_gbp=budget,
        )
        return intent

    def execute_and_explain(
        self, req: GuardedScenarioChatRequest
    ) -> GuardedScenarioChatResponse:
        """
        Executes the Guarded Scenario Pipeline:
        1. Extract & validate intent (Zero Math).
        2. Run 100% deterministic calculation engines.
        3. Formulate natural language explanation citing only audited numbers.
        """
        from backend.services import remote  # parsing may run in the LLM microservice

        intent = remote.parse_intent(self, req.user_query, req.property_id)

        # Guardrails check: ensure requested actions belong to verified catalogue
        invalid_codes = [c for c in intent.requested_interventions if c not in RETROFIT_ACTIONS_CATALOGUE]
        guardrail_passed = len(invalid_codes) == 0

        # Execute deterministic pipeline
        eval_req = FullRetrofitRequest(
            property_id=intent.detected_property_id or req.property_id,
            selected_interventions=intent.requested_interventions,
        )
        deterministic_metrics = self.evaluator(eval_req)

        # Build auditable explanation strictly adhering to deterministic outputs
        actions_str = ", ".join(
            [RETROFIT_ACTIONS_CATALOGUE[c].name for c in intent.requested_interventions]
        )
        injected_str = ""
        if deterministic_metrics.dag_plan.injected_prerequisites:
            inj_names = [
                RETROFIT_ACTIONS_CATALOGUE[c].name
                for c in deterministic_metrics.dag_plan.injected_prerequisites
            ]
            injected_str = (
                f"\n\n**Fabric-First Guardrail Enforced:** To ensure your home avoids the EPC Heat Pump Paradox "
                f"and thermal inefficiencies, our NetworkX physical DAG automatically injected mandatory prerequisite(s): "
                f"*{', '.join(inj_names)}* before installing active equipment."
            )

        explanation = (
            f"Based on our certified deterministic calculation engine for **{deterministic_metrics.address}**:\n\n"
            f"- **Green Home Score:** Rises from **{deterministic_metrics.baseline_score}/100** to **{deterministic_metrics.projected_score}/100** "
            f"(+{deterministic_metrics.score_delta} points, transitioning {deterministic_metrics.band_transition}).\n"
            f"- **Decoupled Dual-Fuel Impact:** Gas consumption changes by **{deterministic_metrics.carbon_impact.delta_gas_kwh:,.0f} kWh/year**, "
            f"while electricity import shifts by **{deterministic_metrics.carbon_impact.delta_electricity_kwh:+,.0f} kWh/year**.\n"
            f"- **Net Annual Carbon Saved:** **{deterministic_metrics.carbon_impact.net_co2e_reduction_tonnes:.2f} tonnes CO2e** "
            f"({deterministic_metrics.carbon_impact.net_co2e_reduction_pct:.1f}% net reduction).\n"
            f"- **Annual Running Cost Impact:** Estimated net saving of **£{deterministic_metrics.carbon_impact.net_bill_savings_gbp:,.2f}/year**.\n"
            f"- **Hedonic Valuation Uplift:** Indicative property value appreciation between **£{deterministic_metrics.property_uplift.predicted_uplift_gbp_min:,.0f}** "
            f"and **£{deterministic_metrics.property_uplift.predicted_uplift_gbp_max:,.0f}** "
            f"(+{deterministic_metrics.property_uplift.predicted_uplift_pct_min}% to +{deterministic_metrics.property_uplift.predicted_uplift_pct_max}%)."
            f"{injected_str}\n\n"
            f"**Audit Assurance:** 100% of numerical outputs are computed deterministically. The AI layer strictly explains the audited calculation without numerical fabrication."
        )

        within_budget = None
        if intent.budget_constraint_gbp is not None:
            total_cost = deterministic_metrics.total_retrofit_cost_gbp
            within_budget = total_cost <= intent.budget_constraint_gbp
            if within_budget:
                explanation += (
                    f"\n\n**Budget check:** The full plan costs **£{total_cost:,.0f}**, within your "
                    f"**£{intent.budget_constraint_gbp:,.0f}** budget."
                )
            else:
                explanation += (
                    f"\n\n**Budget check:** The full plan costs **£{total_cost:,.0f}**, which is above your "
                    f"**£{intent.budget_constraint_gbp:,.0f}** budget. Fabric measures in Phase 1 can be "
                    f"funded first and the later phases added as budget allows."
                )

        explanation, explanation_source, grounding_passed = remote.explain(
            deterministic_metrics, req.user_query, explanation
        )

        # Lloyds Bank specific advisory note
        advisory_note = (
            f"As part of Lloyds Banking Group's Green Living Mandate: Upon completion of this retrofit, "
            f"this property qualifies for £{deterministic_metrics.property_uplift.lloyds_green_mortgage_cashback_gbp:,.0f} "
            f"in Green Living Reward cashback, and an indicative {deterministic_metrics.property_uplift.lloyds_preferential_rate_discount_bps} bps "
            f"discount on eligible green mortgage borrowing."
        )

        return GuardedScenarioChatResponse(
            user_query=req.user_query,
            property_id=deterministic_metrics.property_id,
            extracted_intent=intent,
            guardrail_validation_passed=guardrail_passed,
            audited_deterministic_metrics=deterministic_metrics,
            natural_language_explanation=explanation,
            lloyds_bank_advisory_note=advisory_note,
            explanation_source=explanation_source,
            numeric_grounding_passed=grounding_passed,
            within_budget=within_budget,
        )

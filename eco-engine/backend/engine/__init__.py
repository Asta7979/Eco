"""
Calculation Engines and Guarded LLM layer for Project Eco.
Strictly partitioned: deterministic Python math engines calculate 100% of numbers.
Guarded LLM layer performs zero math.

Exports are resolved lazily (PEP 562), so a microservice that only needs one engine does not
import the others (e.g. the LLM and physics services never load scikit-learn or pandas).
"""

import importlib

_EXPORTS = {
    "calculate_green_home_score": "score_engine",
    "classify_band": "score_engine",
    "solve_retrofit_dag": "dag_engine",
    "RETROFIT_ACTIONS_CATALOGUE": "dag_engine",
    "calculate_decoupled_carbon_impact": "carbon_engine",
    "UK_EF_GAS": "carbon_engine",
    "UK_EF_ELEC": "carbon_engine",
    "predict_property_uplift": "uplift_engine",
    "GuardedScenarioOrchestrator": "llm_guardrails",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name in _EXPORTS:
        return getattr(importlib.import_module(f"{__name__}.{_EXPORTS[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

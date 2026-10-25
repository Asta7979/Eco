"""
Shared deterministic assessment pipeline (Project Eco).

Single source of truth used by the REST API, the Guarded LLM layer and the
Streamlit dashboard, so every surface computes identical numbers:

    detect installed measures -> DAG roadmap -> baseline score -> projected
    attributes -> projected score -> dual-fuel carbon -> hedonic uplift

Also provides FR2 (poor-performance detection and improvement-potential
ranking):

    Improvement Potential = 100 * (E_baseline - E_after) / E_baseline

where E is purchased (delivered) energy, gas + electricity, in kWh/year.
Electricity and gas changes are reported separately so fuel switching stays visible.
"""

from typing import Any, Dict, List, Mapping, Optional

from backend.schemas import (
    CarbonEvaluationRequest,
    DAGSolveRequest,
    ScoreCalculationRequest,
    UpliftEvaluationRequest,
)
from backend.engine.score_engine import calculate_green_home_score
from backend.engine.dag_engine import solve_retrofit_dag
from backend.engine.carbon_engine import calculate_decoupled_carbon_impact
from backend.engine.uplift_engine import predict_property_uplift
from backend.engine.thermal_model import FABRIC_MEASURES, assess_physics

# Every measure a homeowner could take, used to size the "whole-home" potential.
WHOLE_HOME_PACKAGE: List[str] = [
    "wall_insulation",
    "loft_insulation",
    "floor_insulation",
    "window_upgrade",
    "draught_proofing",
    "smart_controls",
    "heat_pump",
    "solar_pv",
    "battery_storage",
]


def detect_installed_actions(prop: Mapping[str, Any]) -> List[str]:
    """Measures the home already has, so the DAG never re-recommends them."""
    installed = []
    glazing = prop["glazing_type"].lower()
    if prop["roof_insulation_depth_mm"] >= 250:
        installed.append("loft_insulation")
    if prop["wall_u_value"] <= 0.45:
        installed.append("wall_insulation")
    if "double" in glazing or "triple" in glazing:
        installed.append("window_upgrade")
    if prop.get("smart_controls", False):
        installed.append("smart_controls")
    if "heat pump" in prop["heating_type"].lower():
        installed.append("heat_pump")
    if prop.get("solar_pv_kwp", 0.0) >= 2.0:
        installed.append("solar_pv")
    if prop.get("battery_storage_kwh", 0.0) >= 4.0:
        installed.append("battery_storage")
    return installed


def _floor_insulated(prop: Mapping[str, Any]) -> bool:
    status = prop["floor_insulation_status"].lower()
    return "insulated" in status and "uninsulated" not in status


def baseline_score_request(prop: Mapping[str, Any]) -> ScoreCalculationRequest:
    return ScoreCalculationRequest(
        property_id=prop["property_id"],
        epc_score=prop["current_epc_score"],
        primary_energy_kwh_m2=prop["current_primary_energy_kwh_m2"],
        wall_u_value=prop["wall_u_value"],
        roof_insulation_depth_mm=prop["roof_insulation_depth_mm"],
        floor_insulated=_floor_insulated(prop),
        heating_type=prop["heating_type"],
        heating_efficiency_pct=prop["heating_efficiency_pct"],
        glazing_type=prop["glazing_type"],
        solar_pv_kwp=prop.get("solar_pv_kwp", 0.0),
        battery_storage_kwh=prop.get("battery_storage_kwh", 0.0),
        smart_controls=prop.get("smart_controls", False),
    )


def projected_score_request(prop: Mapping[str, Any], actions: set) -> ScoreCalculationRequest:
    """Physical attributes of the home after the resolved measures are installed."""
    if "heat_pump" in actions:
        heating_type, heating_eff = "Air Source Heat Pump (ASHP)", 360.0
    else:
        heating_type, heating_eff = prop["heating_type"], prop["heating_efficiency_pct"]

    energy_mult = 1.0
    if "wall_insulation" in actions:
        energy_mult *= 0.72
    if "loft_insulation" in actions:
        energy_mult *= 0.84
    if "window_upgrade" in actions:
        energy_mult *= 0.88
    if "heat_pump" in actions:
        energy_mult *= 0.65
    if "solar_pv" in actions:
        energy_mult *= 0.80

    epc_point_gain = int((1.0 - energy_mult) * 60)

    return ScoreCalculationRequest(
        property_id=prop["property_id"],
        epc_score=min(98, prop["current_epc_score"] + epc_point_gain),
        primary_energy_kwh_m2=max(35.0, prop["current_primary_energy_kwh_m2"] * energy_mult),
        wall_u_value=0.28 if "wall_insulation" in actions else prop["wall_u_value"],
        roof_insulation_depth_mm=300 if "loft_insulation" in actions else prop["roof_insulation_depth_mm"],
        floor_insulated=True if "floor_insulation" in actions else _floor_insulated(prop),
        heating_type=heating_type,
        heating_efficiency_pct=heating_eff,
        glazing_type="Triple Glazed Passivhaus Standard" if "window_upgrade" in actions else prop["glazing_type"],
        solar_pv_kwp=prop.get("solar_pv_kwp", 0.0) + (4.0 if "solar_pv" in actions else 0.0),
        battery_storage_kwh=prop.get("battery_storage_kwh", 0.0) + (8.0 if "battery_storage" in actions else 0.0),
        smart_controls=True if "smart_controls" in actions else prop.get("smart_controls", False),
    )


def run_full_assessment(prop: Mapping[str, Any], selected_interventions: List[str], per_measure: bool = True) -> Dict[str, Any]:
    """Runs all four deterministic engines for one property and a set of measures."""
    dag = solve_retrofit_dag(
        DAGSolveRequest(
            property_id=prop["property_id"],
            requested_interventions=selected_interventions,
            current_installed_actions=detect_installed_actions(prop),
        )
    )
    actions = set(dag.full_resolved_action_codes)

    base_score = calculate_green_home_score(baseline_score_request(prop))
    proj_score = calculate_green_home_score(projected_score_request(prop, actions))

    # Building physics: per-home fabric savings (hourly model, calibrated to the meter).
    physics = assess_physics(prop, [a for a in actions if a in FABRIC_MEASURES], per_measure=per_measure)

    carbon = calculate_decoupled_carbon_impact(
        CarbonEvaluationRequest(
            baseline_gas_kwh=prop["annual_gas_kwh"],
            baseline_electricity_kwh=prop["annual_electricity_kwh"],
            interventions=list(actions),
            property_floor_area_m2=prop["total_floor_area_m2"],
            has_insulation=prop["wall_u_value"] < 0.60 or prop["roof_insulation_depth_mm"] >= 200,
            fabric_reduction_factor=physics.realised_reduction_factor,
        )
    )

    uplift = predict_property_uplift(
        UpliftEvaluationRequest(
            baseline_value_gbp=prop["baseline_value_gbp"],
            baseline_score=max(1, base_score.headline_score),
            projected_score=max(1, proj_score.headline_score),
            floor_area_m2=prop["total_floor_area_m2"],
            region=prop["region"],
            property_type=prop["property_type"],
        )
    )

    return {
        "dag": dag,
        "base_score": base_score,
        "proj_score": proj_score,
        "carbon": carbon,
        "uplift": uplift,
        "physics": physics,
    }


def score_drivers(score_result, limit: int = 3) -> List[str]:
    """
    Physical drivers of a low score: the components losing the most weighted
    points against a perfect 100, worst first.
    """
    lost = sorted(
        score_result.components,
        key=lambda c: c.weight * (100.0 - c.sub_score),
        reverse=True,
    )
    return [c.name for c in lost if c.sub_score < 60.0][:limit]


def improvement_potential(prop: Mapping[str, Any]) -> Dict[str, Any]:
    """
    FR2 improvement potential under the whole-home package. Returns None for the
    percentage when the baseline is missing or zero; that is never treated as 0%.
    """
    result = run_full_assessment(prop, WHOLE_HOME_PACKAGE, per_measure=False)
    carbon = result["carbon"]
    e_base = carbon.baseline_gas_kwh + carbon.baseline_electricity_kwh
    e_after = carbon.projected_gas_kwh + carbon.projected_electricity_kwh
    potential: Optional[float] = (
        round(100.0 * (e_base - e_after) / e_base, 1) if e_base > 0 else None
    )
    return {
        "assessment": result,
        "baseline_energy_kwh": round(e_base, 1),
        "after_energy_kwh": round(e_after, 1),
        "improvement_potential_pct": potential,
        "delta_gas_kwh": carbon.delta_gas_kwh,
        "delta_electricity_kwh": carbon.delta_electricity_kwh,
    }

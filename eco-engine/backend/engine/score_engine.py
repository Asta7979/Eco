"""
7-Component Green Home Score Deterministic Engine (Project Eco).
Target Organization: Lloyds Banking Group.

Headline equation:
    S = round(sum_{k=1}^{7} w_k * s_k)

Component Weights:
    1. Energy Efficiency (25%, w_1 = 0.25)
    2. Energy Consumption Intensity (20%, w_2 = 0.20)
    3. Insulation Quality (15%, w_3 = 0.15)
    4. Heating System (15%, w_4 = 0.15)
    5. Windows & Glazing (10%, w_5 = 0.10)
    6. Renewable Energy (10%, w_6 = 0.10)
    7. Smart Energy Controls (5%, w_7 = 0.05)

Monotonicity Constraint:
    All sub-score mapping functions guarantee that improving any physical attribute
    strictly increases or preserves the resulting sub-score (ds_k/dx >= 0).
"""

from typing import Dict, List, Tuple, Any
from backend.schemas import (
    ComponentScoreBreakdown,
    ScoreCalculationRequest,
    ScoreCalculationResponse,
)

WEIGHTS: Dict[str, float] = {
    "energy_efficiency": 0.25,
    "energy_consumption_intensity": 0.20,
    "insulation_quality": 0.15,
    "heating_system": 0.15,
    "windows_and_glazing": 0.10,
    "renewable_energy": 0.10,
    "smart_controls": 0.05,
}

# Assert weights sum to 1.0
assert round(sum(WEIGHTS.values()), 4) == 1.0, "Component weights must sum precisely to 1.0"


def subscore_energy_efficiency(epc_score: int) -> float:
    """Monotonic mapping of standard EPC score (1-100) to subscore."""
    clamped = max(1, min(100, epc_score))
    return float(clamped)


def subscore_energy_intensity(primary_energy_kwh_m2: float) -> float:
    """
    Monotonic mapping of primary energy intensity (kWh/m²/year).
    Best in class <= 50 kWh/m² -> 100.0
    Severe draught/loss >= 450 kWh/m² -> 0.0
    """
    kwh = max(0.0, float(primary_energy_kwh_m2))
    if kwh <= 50.0:
        return 100.0
    if kwh >= 450.0:
        return 0.0
    # Linear monotonic interpolation
    return round(100.0 - ((kwh - 50.0) / 400.0) * 100.0, 2)


def subscore_insulation_quality(
    wall_u_value: float,
    roof_insulation_depth_mm: int,
    floor_insulated: bool = False,
) -> float:
    """
    Monotonic evaluation of thermal envelope:
    - Wall U-value (0.15 W/m²K Passivhaus to 2.6 W/m²K Solid Stone)
    - Roof insulation depth (0mm to 350mm+)
    - Floor insulation presence
    """
    # 1. Wall component (50% of insulation score)
    # Target modern standard is 0.18-0.30, uninsulated is 1.6-2.6
    u = max(0.15, min(2.8, float(wall_u_value)))
    # Invert U-value: lower U-value = higher insulation
    wall_sub = max(0.0, min(100.0, 100.0 - ((u - 0.15) / (2.6 - 0.15)) * 100.0))

    # 2. Roof component (35% of insulation score)
    # UK standard recommendation is 270mm-300mm
    depth = max(0, min(350, int(roof_insulation_depth_mm)))
    roof_sub = (depth / 300.0) * 100.0
    roof_sub = max(0.0, min(100.0, roof_sub))

    # 3. Floor component (15% of insulation score)
    floor_sub = 100.0 if floor_insulated else 20.0

    composite = (0.50 * wall_sub) + (0.35 * roof_sub) + (0.15 * floor_sub)
    return round(max(0.0, min(100.0, composite)), 2)


def subscore_heating_system(heating_type: str, heating_efficiency_pct: float) -> float:
    """
    Monotonic evaluation of heating decarbonisation & efficiency.
    Heat pumps / district low carbon achieve highest tiers.
    """
    raw = (heating_type or "").lower()
    h_type = raw.replace("_", " ").replace("-", " ")
    eff = max(40.0, float(heating_efficiency_pct))

    if "heat pump" in h_type or "ashp" in h_type or "gshp" in h_type:
        # COP 3.0 to 4.0 (300% to 400% efficiency)
        if eff >= 350.0:
            return 100.0
        elif eff >= 300.0:
            return 92.0
        else:
            return 85.0
    elif "district" in h_type or "chp" in h_type:
        return 80.0
    elif "non condensing" in h_type or "standard" in h_type:
        return 40.0
    elif "condensing" in h_type and "modern" in h_type:
        return 72.0
    elif "condensing" in h_type:
        return 62.0
    elif "electric" in h_type and "storage" in h_type:
        return 35.0
    elif "oil" in h_type or "lpg" in h_type:
        return 28.0
    elif "resistive" in h_type or "panel" in h_type:
        return 22.0
    else:
        # Generic efficiency clamp
        if eff > 90:
            return 65.0
        elif eff > 80:
            return 50.0
        else:
            return 30.0


def subscore_windows_and_glazing(glazing_type: str) -> float:
    """Monotonic evaluation of window fenestration."""
    g = (glazing_type or "").lower()
    if "triple" in g or "passivhaus" in g:
        return 100.0
    elif "modern double" in g or "post-2002" in g or "high performance" in g:
        return 78.0
    elif "double" in g or "pre-2002" in g:
        return 52.0
    elif "secondary" in g:
        return 36.0
    elif "single" in g:
        return 12.0
    return 40.0


def subscore_renewable_energy(solar_pv_kwp: float, battery_storage_kwh: float) -> float:
    """
    Monotonic evaluation of on-site renewables & flexibility.
    Typical UK array 3.5 - 4.5 kWp with 5 - 10 kWh battery.
    """
    pv = max(0.0, float(solar_pv_kwp))
    batt = max(0.0, float(battery_storage_kwh))

    # PV points up to 70
    pv_score = min(70.0, (pv / 4.0) * 70.0)

    # Battery points up to 30
    batt_score = min(30.0, (batt / 8.0) * 30.0)

    total = pv_score + batt_score
    return round(min(100.0, total), 2)


def subscore_smart_controls(has_smart_controls: bool) -> float:
    """Monotonic evaluation of smart thermostats, TRVs, and zone control."""
    return 100.0 if bool(has_smart_controls) else 20.0


def classify_band(score: int) -> Tuple[str, bool]:
    """
    Classify headline score into Lloyd's Green Mortgage bands:
    - Highly Efficient: 80–100
    - Good: 65–79
    - Moderate: 50–64
    - Poor: 30–49 (Flagged)
    - Very Poor: 0–29 (Flagged)
    """
    s = max(0, min(100, int(score)))
    if s >= 80:
        return "Highly Efficient", False
    elif s >= 65:
        return "Good", False
    elif s >= 50:
        return "Moderate", False
    elif s >= 30:
        return "Poor", True
    else:
        return "Very Poor", True


def calculate_green_home_score(req: ScoreCalculationRequest) -> ScoreCalculationResponse:
    """
    Computes deterministic headline Green Home Score and 7 subcomponent scores.
    """
    s1 = subscore_energy_efficiency(req.epc_score)
    s2 = subscore_energy_intensity(req.primary_energy_kwh_m2)
    s3 = subscore_insulation_quality(req.wall_u_value, req.roof_insulation_depth_mm, req.floor_insulated)
    s4 = subscore_heating_system(req.heating_type, req.heating_efficiency_pct)
    s5 = subscore_windows_and_glazing(req.glazing_type)
    s6 = subscore_renewable_energy(req.solar_pv_kwp, req.battery_storage_kwh)
    s7 = subscore_smart_controls(req.smart_controls)

    components = [
        ComponentScoreBreakdown(
            name="Energy Efficiency (EPC SAP)",
            weight=WEIGHTS["energy_efficiency"],
            sub_score=s1,
            weighted_score=round(s1 * WEIGHTS["energy_efficiency"], 2),
            description="Official SAP / RdSAP energy efficiency metric",
        ),
        ComponentScoreBreakdown(
            name="Energy Consumption Intensity",
            weight=WEIGHTS["energy_consumption_intensity"],
            sub_score=s2,
            weighted_score=round(s2 * WEIGHTS["energy_consumption_intensity"], 2),
            description="Normalized thermal and electrical intensity in kWh/m²/year",
        ),
        ComponentScoreBreakdown(
            name="Insulation Quality",
            weight=WEIGHTS["insulation_quality"],
            sub_score=s3,
            weighted_score=round(s3 * WEIGHTS["insulation_quality"], 2),
            description="Thermal envelope U-values across walls, roof, and floor",
        ),
        ComponentScoreBreakdown(
            name="Heating System",
            weight=WEIGHTS["heating_system"],
            sub_score=s4,
            weighted_score=round(s4 * WEIGHTS["heating_system"], 2),
            description="Low-carbon efficiency (Heat Pump vs Gas vs Resistive)",
        ),
        ComponentScoreBreakdown(
            name="Windows & Glazing",
            weight=WEIGHTS["windows_and_glazing"],
            sub_score=s5,
            weighted_score=round(s5 * WEIGHTS["windows_and_glazing"], 2),
            description="Acoustic and low-emissivity double/triple glazing rating",
        ),
        ComponentScoreBreakdown(
            name="Renewable Energy",
            weight=WEIGHTS["renewable_energy"],
            sub_score=s6,
            weighted_score=round(s6 * WEIGHTS["renewable_energy"], 2),
            description="On-site Solar Photovoltaics (kWp) and Battery Storage (kWh)",
        ),
        ComponentScoreBreakdown(
            name="Smart Energy Controls",
            weight=WEIGHTS["smart_controls"],
            sub_score=s7,
            weighted_score=round(s7 * WEIGHTS["smart_controls"], 2),
            description="Smart thermostat, zone scheduling, and smart TRVs",
        ),
    ]

    total_weighted = sum(c.weighted_score for c in components)
    headline = int(round(total_weighted))
    headline = max(0, min(100, headline))

    band, flagged = classify_band(headline)

    return ScoreCalculationResponse(
        headline_score=headline,
        band=band,
        flagged_for_intervention=flagged,
        components=components,
        monotonicity_guaranteed=True,
    )

"""
Unit tests for 7-Component Green Home Score Deterministic Math Engine.
Verifies monotonicity constraints, component weights, and band classifications.
"""

import pytest
from backend.engine.score_engine import (
    calculate_green_home_score,
    subscore_energy_efficiency,
    subscore_energy_intensity,
    subscore_insulation_quality,
    subscore_heating_system,
    subscore_windows_and_glazing,
    subscore_renewable_energy,
    subscore_smart_controls,
    classify_band,
    WEIGHTS,
)
from backend.schemas import ScoreCalculationRequest


def test_weights_sum_to_one():
    """Verify that all 7 component weights sum strictly to 1.0."""
    total_weight = sum(WEIGHTS.values())
    assert pytest.approx(total_weight, 0.0001) == 1.0
    assert len(WEIGHTS) == 7
    assert WEIGHTS["energy_efficiency"] == 0.25
    assert WEIGHTS["energy_consumption_intensity"] == 0.20
    assert WEIGHTS["insulation_quality"] == 0.15
    assert WEIGHTS["heating_system"] == 0.15
    assert WEIGHTS["windows_and_glazing"] == 0.10
    assert WEIGHTS["renewable_energy"] == 0.10
    assert WEIGHTS["smart_controls"] == 0.05


def test_monotonicity_energy_efficiency():
    """Improving EPC score must monotonically increase or maintain subscore."""
    scores = [20, 35, 50, 68, 85, 95]
    subscores = [subscore_energy_efficiency(s) for s in scores]
    for i in range(len(subscores) - 1):
        assert subscores[i] <= subscores[i + 1]


def test_monotonicity_energy_intensity():
    """Decreasing kWh/m² intensity (better efficiency) must increase subscore."""
    intensities = [450.0, 350.0, 250.0, 150.0, 50.0]  # Lower is better
    subscores = [subscore_energy_intensity(kwh) for kwh in intensities]
    for i in range(len(subscores) - 1):
        assert subscores[i] <= subscores[i + 1]


def test_monotonicity_insulation_quality():
    """Improving wall U-value and roof depth must strictly increase insulation score."""
    # Decreasing U-value from 2.4 down to 0.20
    s_poor = subscore_insulation_quality(wall_u_value=2.4, roof_insulation_depth_mm=0, floor_insulated=False)
    s_mid = subscore_insulation_quality(wall_u_value=1.5, roof_insulation_depth_mm=100, floor_insulated=False)
    s_good = subscore_insulation_quality(wall_u_value=0.5, roof_insulation_depth_mm=250, floor_insulated=False)
    s_high = subscore_insulation_quality(wall_u_value=0.20, roof_insulation_depth_mm=300, floor_insulated=True)

    assert s_poor < s_mid < s_good < s_high


def test_monotonicity_heating_system():
    """Low carbon systems (Heat pump) must score higher than gas and resistive electric."""
    s_resistive = subscore_heating_system("electric_resistive", 100.0)
    s_gas_noncond = subscore_heating_system("gas_boiler_non_condensing", 70.0)
    s_gas_cond = subscore_heating_system("gas_boiler_condensing", 88.0)
    s_heat_pump = subscore_heating_system("heat_pump_ashp", 360.0)

    assert s_resistive < s_gas_cond
    assert s_gas_noncond < s_gas_cond
    assert s_gas_cond < s_heat_pump


def test_monotonicity_renewables():
    """Adding Solar PV and battery storage must monotonically increase score."""
    s0 = subscore_renewable_energy(solar_pv_kwp=0.0, battery_storage_kwh=0.0)
    s1 = subscore_renewable_energy(solar_pv_kwp=2.0, battery_storage_kwh=0.0)
    s2 = subscore_renewable_energy(solar_pv_kwp=4.0, battery_storage_kwh=0.0)
    s3 = subscore_renewable_energy(solar_pv_kwp=4.0, battery_storage_kwh=8.0)

    assert s0 < s1 < s2 < s3


def test_band_classification_and_intervention_flag():
    """Verify official Lloyds band thresholds and automatic intervention flags."""
    assert classify_band(85) == ("Highly Efficient", False)
    assert classify_band(70) == ("Good", False)
    assert classify_band(55) == ("Moderate", False)
    assert classify_band(42) == ("Poor", True)
    assert classify_band(20) == ("Very Poor", True)


def test_full_headline_score_calculation():
    """Verify end-to-end headline calculation and component response schema."""
    req = ScoreCalculationRequest(
        property_id="TEST-001",
        epc_score=48,
        primary_energy_kwh_m2=310.0,
        wall_u_value=1.65,
        roof_insulation_depth_mm=75,
        floor_insulated=False,
        heating_type="Gas Boiler (Non-condensing)",
        heating_efficiency_pct=74.0,
        glazing_type="Single Glazed",
        solar_pv_kwp=0.0,
        battery_storage_kwh=0.0,
        smart_controls=False,
    )
    res = calculate_green_home_score(req)

    assert 0 <= res.headline_score <= 100
    assert res.band in ["Poor", "Moderate"]
    assert res.flagged_for_intervention is True
    assert len(res.components) == 7
    assert res.monotonicity_guaranteed is True

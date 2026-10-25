"""
Unit tests for Decoupled Dual-Fuel Carbon Accounting Engine.
Verifies official UK GHG conversion factors, decoupled fuel vectors, and EPC Heat Pump Paradox logic.
"""

import pytest
from backend.engine.carbon_engine import (
    calculate_decoupled_carbon_impact,
    UK_EF_GAS,
    UK_EF_ELEC,
)
from backend.schemas import CarbonEvaluationRequest


def test_official_uk_ghg_conversion_factors():
    """Verify that official UK DESNZ/DEFRA carbon factors are strictly applied."""
    assert UK_EF_GAS == 0.18316
    assert UK_EF_ELEC == 0.20705


def test_decoupled_fuel_accounting_vectors():
    """
    Gas elimination (delta_gas < 0) and electricity import increase (delta_elec > 0)
    must be decoupled and tracked independently.
    """
    req = CarbonEvaluationRequest(
        baseline_gas_kwh=16000.0,
        baseline_electricity_kwh=3500.0,
        interventions=["heat_pump"],
        property_floor_area_m2=100.0,
        has_insulation=True,
    )
    res = calculate_decoupled_carbon_impact(req)

    # Gas must be eliminated
    assert res.delta_gas_kwh < 0
    assert res.projected_gas_kwh == 0.0

    # Electricity import must increase due to heat pump
    assert res.delta_electricity_kwh > 0
    assert res.projected_electricity_kwh > res.baseline_electricity_kwh

    # Carbon savings must be substantial (> 50% reduction)
    assert res.net_co2e_reduction_kg > 0
    assert res.net_co2e_reduction_pct > 50.0

    # Decoupled equation check
    expected_delta_co2e = (res.delta_gas_kwh * UK_EF_GAS) + (res.delta_electricity_kwh * UK_EF_ELEC)
    assert pytest.approx(-res.net_co2e_reduction_kg, 1.0) == expected_delta_co2e


def test_epc_heat_pump_paradox_detected_when_uninsulated():
    """
    Installing a heat pump without insulation leads to high power demand,
    triggering the EPC Heat Pump Paradox warning.
    """
    req = CarbonEvaluationRequest(
        baseline_gas_kwh=22000.0,
        baseline_electricity_kwh=3800.0,
        interventions=["heat_pump"],
        property_floor_area_m2=120.0,
        has_insulation=False,  # Uninsulated envelope!
    )
    res = calculate_decoupled_carbon_impact(req)

    assert res.heat_pump_paradox_detected is True
    assert "EPC Heat Pump Paradox Active" in res.paradox_explanation


def test_epc_heat_pump_paradox_mitigated_with_fabric_first():
    """
    Combining heat pump with fabric insulation reduces thermal load, lowers bills,
    and solves the paradox.
    """
    req = CarbonEvaluationRequest(
        baseline_gas_kwh=22000.0,
        baseline_electricity_kwh=3800.0,
        interventions=["heat_pump", "wall_insulation", "loft_insulation"],
        property_floor_area_m2=120.0,
        has_insulation=False,
    )
    res = calculate_decoupled_carbon_impact(req)

    assert res.heat_pump_paradox_mitigated is True
    assert "EPC Heat Pump Paradox Solved" in res.paradox_explanation
    assert res.net_co2e_reduction_tonnes > 2.5


def test_solar_does_not_increase_imports_for_low_use_home():
    res = calculate_decoupled_carbon_impact(CarbonEvaluationRequest(
        baseline_gas_kwh=0.0,
        baseline_electricity_kwh=500.0,
        interventions=["solar_pv"],
        property_floor_area_m2=80.0,
    ))
    assert res.projected_electricity_kwh == 0.0
    assert res.delta_electricity_kwh == -500.0
    assert res.net_bill_savings_gbp > 0

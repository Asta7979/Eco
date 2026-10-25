"""
Tests for the fast hourly building-physics model (simple-simple 1R1C + HEM weather) and its
integration into the assessment pipeline.
"""

import json
import time
from pathlib import Path

import pytest

from backend.engine import thermal_model as tm
from backend.engine.assessment import run_full_assessment
from backend.engine.carbon_engine import calculate_decoupled_carbon_impact
from backend.schemas import CarbonEvaluationRequest

PROPS = {p["property_id"]: p for p in json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "properties.json").read_text(encoding="utf-8"))}
VICTORIAN = PROPS["PROP-GB-001"]
FABRIC = ["wall_insulation", "loft_insulation", "floor_insulation", "window_upgrade", "draught_proofing", "smart_controls"]


def test_weather_file_is_a_full_uk_year():
    temps, solar = tm._london_weather()
    assert len(temps) == len(solar) == 8760
    assert 9.0 < sum(temps) / len(temps) < 13.0          # London annual mean
    assert max(solar) <= 1000.0 and min(solar) >= 0.0    # corrupt DNI value capped


def test_colder_regions_need_more_heat():
    geo, fab = tm.geometry_for(VICTORIAN), tm.fabric_for(VICTORIAN)
    assert tm.simulate_heating_kwh(geo, fab, "Scotland") > tm.simulate_heating_kwh(geo, fab, "London")


@pytest.mark.parametrize("measure", FABRIC)
def test_every_fabric_measure_reduces_heating_need(measure):
    r = tm.assess_physics(VICTORIAN, [measure])
    assert r.heating_need_after_kwh < r.heating_need_before_kwh, measure
    assert r.per_measure_saving_pct[measure] > 0


def test_floor_insulation_now_saves_energy_in_the_pipeline():
    without = run_full_assessment(VICTORIAN, ["loft_insulation"])["carbon"]
    with_floor = run_full_assessment(VICTORIAN, ["loft_insulation", "floor_insulation"])["carbon"]
    assert with_floor.projected_gas_kwh < without.projected_gas_kwh


def test_combined_measures_save_more_than_any_single_one_but_less_than_the_sum():
    r = tm.assess_physics(VICTORIAN, FABRIC)
    combined = 100 * (1 - r.fabric_reduction_factor)
    singles = r.per_measure_saving_pct.values()
    assert combined > max(singles)
    assert combined < sum(singles)  # interactions: savings overlap, never simply add


def test_heat_loss_and_heat_pump_sizing_are_consistent():
    r = tm.assess_physics(VICTORIAN, FABRIC)
    assert r.heat_loss_after_w_per_k < r.heat_loss_w_per_k
    expected_kw = r.heat_loss_after_w_per_k * (tm.DESIGN_INDOOR_C - tm.DESIGN_OUTDOOR_C) / 1000
    assert abs(r.design_heat_loss_after_kw - expected_kw) < 0.01
    assert r.recommended_heat_pump_kw >= expected_kw and r.recommended_heat_pump_kw >= 3


def test_savings_are_calibrated_to_the_meter():
    r = tm.assess_physics(VICTORIAN, FABRIC)
    assert r.calibrated_to_meter
    assert tm.PREBOUND_MIN <= r.prebound_ratio <= tm.PREBOUND_MAX
    physics_saving = 1 - r.fabric_reduction_factor
    realised_saving = 1 - r.realised_reduction_factor
    assert realised_saving == pytest.approx(physics_saving * r.prebound_ratio, abs=1e-3)


def test_homes_without_gas_use_the_default_prebound():
    electric = PROPS["PROP-GB-013"]  # LPG / electric heating, no gas meter
    r = tm.assess_physics(electric, ["loft_insulation"])
    assert not r.calibrated_to_meter and r.prebound_ratio == tm.PREBOUND_DEFAULT


def test_worse_homes_show_a_larger_prebound_gap():
    poor = tm.assess_physics(PROPS["PROP-GB-002"], [])   # EPC F
    good = tm.assess_physics(PROPS["PROP-GB-016"], [])   # EPC B
    assert poor.prebound_ratio < good.prebound_ratio


def test_carbon_engine_prefers_the_physics_factor():
    base = dict(baseline_gas_kwh=15000, baseline_electricity_kwh=3000, interventions=["wall_insulation"], property_floor_area_m2=90)
    catalogue = calculate_decoupled_carbon_impact(CarbonEvaluationRequest(**base))
    physics = calculate_decoupled_carbon_impact(CarbonEvaluationRequest(**base, fabric_reduction_factor=0.5))
    assert physics.projected_gas_kwh == pytest.approx(15000 * 0.9 * 0.5 + 15000 * 0.1, abs=0.5)
    assert catalogue.projected_gas_kwh == pytest.approx(15000 * 0.9 * 0.70 + 15000 * 0.1, abs=0.5)


def test_physics_is_fast_enough_for_live_what_ifs():
    tm.simulate_heating_kwh.cache_clear()
    t0 = time.perf_counter()
    for p in PROPS.values():
        tm.assess_physics(p, FABRIC)
    assert time.perf_counter() - t0 < 2.0  # 20 homes x 7 hourly simulations

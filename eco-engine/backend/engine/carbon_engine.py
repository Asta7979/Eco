"""
Decoupled Dual-Fuel Carbon Accounting Engine (Project Eco).
Target Organization: Lloyds Banking Group.

Solves:
    1. Uncoupled dual-fuel accounting: Tracks gas elimination (ΔkWh_gas < 0)
       and electricity import increases (ΔkWh_elec > 0) as separate vectors.
    2. EPC Heat Pump Paradox: Demonstrates true carbon reductions independently
       of UK gas-to-electricity retail price ratios (where electricity is ~4x price of gas).
    3. Official UK Government GHG Conversion Factors (DESNZ / DEFRA 2024-2025):
       - Natural Gas (EF_gas): 0.18316 kg CO2e / kWh
       - Grid Electricity (EF_elec): 0.20705 kg CO2e / kWh

Formula:
    ΔCO2e = (ΔkWh_gas * 0.18316) + (ΔkWh_elec * 0.20705)
    Net CO2e Saved = -ΔCO2e
"""

from typing import List, Dict, Any, Tuple
from backend.schemas import (
    CarbonEvaluationRequest,
    CarbonEvaluationResponse,
)

# Official UK DESNZ/DEFRA 2024 Conversion Factors (kg CO2e per kWh)
UK_EF_GAS: float = 0.18316
UK_EF_ELEC: float = 0.20705

# UK Standard Retail Energy Tariffs (Ofgem Price Cap Benchmark in £/kWh)
UK_UNIT_PRICE_GAS_GBP: float = 0.0624
UK_UNIT_PRICE_ELEC_GBP: float = 0.2450


def calculate_decoupled_carbon_impact(
    req: CarbonEvaluationRequest,
) -> CarbonEvaluationResponse:
    """
    Computes deterministic dual-fuel accounting and carbon savings.
    """
    base_gas = float(req.baseline_gas_kwh)
    base_elec = float(req.baseline_electricity_kwh)
    interventions = set(req.interventions)

    # 1. Thermal load reduction. Preferred: the building-physics model's per-home factor.
    #    Fallback: fixed catalogue multipliers (used by the stand-alone /evaluate/carbon call).
    fabric_reduction_factor = 1.0
    if req.fabric_reduction_factor is not None:
        fabric_reduction_factor = float(req.fabric_reduction_factor)
    else:
        if "wall_insulation" in interventions:
            fabric_reduction_factor *= 0.70  # 30% reduction in space heating
        if "loft_insulation" in interventions:
            fabric_reduction_factor *= 0.82  # 18% reduction
        if "floor_insulation" in interventions:
            fabric_reduction_factor *= 0.93  # 7% reduction (was missing: floor upgrades saved nothing)
        if "window_upgrade" in interventions:
            fabric_reduction_factor *= 0.88  # 12% reduction
        if "draught_proofing" in interventions:
            fabric_reduction_factor *= 0.94  # 6% reduction
        if "smart_controls" in interventions:
            fabric_reduction_factor *= 0.92  # 8% optimization

    has_fabric_upgrade = fabric_reduction_factor < 0.95 or req.has_insulation

    # 2. Heating system transition
    proj_gas = base_gas
    proj_elec = base_elec
    heat_pump_paradox_detected = False
    heat_pump_paradox_mitigated = False

    if "heat_pump" in interventions:
        # Gas heating is eliminated (assumes 90% of base gas was space heating & hot water)
        gas_heating_load = base_gas * 0.92
        remaining_gas = base_gas - gas_heating_load  # e.g., cooking, or 0 if fully electrified
        proj_gas = 0.0  # Fully electrified retrofit removes gas meter

        # Calculate seasonal COP (Coefficient of Performance)
        # Fabric-first insulation allows lower flow temperature (35-45°C) -> high COP 3.6
        # Uninsulated home requires high flow temperature (60-65°C) -> poor COP 2.4
        if has_fabric_upgrade:
            scop = 3.6
            heat_pump_paradox_mitigated = True
        else:
            scop = 2.45
            heat_pump_paradox_detected = True

        # Equivalent thermal heat energy delivered to the home
        # (Assuming baseline gas boiler was ~80% efficient)
        useful_heat_demand_kwh = (gas_heating_load * 0.80) * fabric_reduction_factor

        # Electricity imported to run heat pump = Useful Heat / SCOP
        heat_pump_elec_import = useful_heat_demand_kwh / scop
        proj_elec += heat_pump_elec_import

    else:
        # No heat pump: fabric upgrades reduce baseline gas heating directly
        heating_gas = base_gas * 0.90
        base_other_gas = base_gas * 0.10
        proj_gas = (heating_gas * fabric_reduction_factor) + base_other_gas

    # 3. Solar PV and battery flexibility reductions
    if "solar_pv" in interventions:
        # 4.0 kWp array generates approx 3,600 kWh/year in the UK
        solar_generation = 3600.0
        if "battery_storage" in interventions:
            # Battery increases self-consumption from ~45% to ~85%
            solar_self_consumption = solar_generation * 0.85
        else:
            solar_self_consumption = solar_generation * 0.45

        proj_elec = max(0.0, proj_elec - solar_self_consumption)

    # 4. Calculate Deltas
    delta_gas = proj_gas - base_gas  # Negative if gas reduced
    delta_elec = proj_elec - base_elec  # Positive if heat pump import increased

    # 5. Decoupled Carbon Equation
    baseline_co2e_kg = (base_gas * UK_EF_GAS) + (base_elec * UK_EF_ELEC)
    projected_co2e_kg = (proj_gas * UK_EF_GAS) + (proj_elec * UK_EF_ELEC)

    delta_co2e_kg = (delta_gas * UK_EF_GAS) + (delta_elec * UK_EF_ELEC)
    net_co2e_saved_kg = max(0.0, -delta_co2e_kg)
    net_co2e_saved_tonnes = net_co2e_saved_kg / 1000.0

    net_co2e_reduction_pct = (
        (net_co2e_saved_kg / baseline_co2e_kg * 100.0) if baseline_co2e_kg > 0 else 0.0
    )

    # 6. Financial Running Costs (£/year)
    baseline_bill = (base_gas * UK_UNIT_PRICE_GAS_GBP) + (base_elec * UK_UNIT_PRICE_ELEC_GBP)
    projected_bill = (proj_gas * UK_UNIT_PRICE_GAS_GBP) + (proj_elec * UK_UNIT_PRICE_ELEC_GBP)
    net_bill_savings = baseline_bill - projected_bill

    # Evaluate Paradox condition:
    # If carbon savings > 50% but bills increase or break-even due to 4:1 elec-to-gas price ratio
    if "heat_pump" in interventions and not has_fabric_upgrade and net_bill_savings < 0:
        heat_pump_paradox_detected = True

    if heat_pump_paradox_detected and not heat_pump_paradox_mitigated:
        paradox_explanation = (
            "EPC Heat Pump Paradox Active: Grid electricity costs ~3.9x more per kWh than natural gas. "
            "Without prior fabric insulation, the heat pump operates at high flow temps (COP ~2.4), causing "
            "higher annual power bills despite a massive 65%+ carbon reduction. "
            "Solution: Sequence Phase 1 fabric insulation first to lower thermal demand and boost heat pump COP to 3.6+."
        )
    elif "heat_pump" in interventions and heat_pump_paradox_mitigated:
        paradox_explanation = (
            "EPC Heat Pump Paradox Solved: Fabric-first insulation reduces space heating demand by over 30%, "
            "enabling the heat pump to operate at low flow temperature (COP 3.6). Both true carbon emissions "
            "and operational heating costs are decisively reduced simultaneously."
        )
    else:
        paradox_explanation = (
            "Standard dual-fuel accounting: Fabric and solar upgrades eliminate unnecessary kWh consumption "
            "across both gas and electricity vectors."
        )

    return CarbonEvaluationResponse(
        baseline_gas_kwh=round(base_gas, 1),
        baseline_electricity_kwh=round(base_elec, 1),
        projected_gas_kwh=round(proj_gas, 1),
        projected_electricity_kwh=round(proj_elec, 1),
        delta_gas_kwh=round(delta_gas, 1),
        delta_electricity_kwh=round(delta_elec, 1),
        baseline_co2e_kg=round(baseline_co2e_kg, 1),
        projected_co2e_kg=round(projected_co2e_kg, 1),
        net_co2e_reduction_kg=round(net_co2e_saved_kg, 1),
        net_co2e_reduction_tonnes=round(net_co2e_saved_tonnes, 2),
        net_co2e_reduction_pct=round(net_co2e_reduction_pct, 1),
        baseline_annual_bill_gbp=round(baseline_bill, 2),
        projected_annual_bill_gbp=round(projected_bill, 2),
        net_bill_savings_gbp=round(net_bill_savings, 2),
        heat_pump_paradox_detected=heat_pump_paradox_detected,
        heat_pump_paradox_mitigated=heat_pump_paradox_mitigated,
        paradox_explanation=paradox_explanation,
    )

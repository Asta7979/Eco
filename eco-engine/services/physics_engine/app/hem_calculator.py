"""
Physics engine: fast calculator (named after the UK Home Energy Model it draws data from).

What comes from where:
* Weather: hourly London year from communitiesuk/epb-home-energy-model (MIT, Crown copyright),
  shifted per UK region. The HEM Rust engine itself is not run: it is functionally incomplete
  and its input format is undocumented (see the repository README).
* Hourly heat model: simple-simple 1R1C (timtroendle/simple-simple, MIT; ISO 13790 derived),
  extended with gains, ventilation and a SAP/HEM heating schedule (backend.engine.thermal_model).
* Carbon factors: DESNZ / DEFRA GHG conversion factors, the same values the gateway's carbon
  engine uses, so every service reports identical coefficients.
"""

from dataclasses import asdict
from typing import Dict, List, Mapping

from backend.engine.carbon_engine import UK_EF_ELEC, UK_EF_GAS
from backend.engine.thermal_model import FABRIC_MEASURES, assess_physics

CARBON_FACTORS_KG_PER_KWH = {"gas": UK_EF_GAS, "electricity": UK_EF_ELEC, "source": "DESNZ / DEFRA GHG conversion factors"}


def simulate_fast(prop: Mapping, measures: List[str]) -> Dict:
    result = asdict(assess_physics(prop, [m for m in measures if m in FABRIC_MEASURES]))
    result["carbon_factors"] = CARBON_FACTORS_KG_PER_KWH
    return result

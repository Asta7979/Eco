"""
Fast hourly building-physics model (Project Eco).

Core equation from timtroendle/simple-simple (MIT), itself derived from the hourly dynamic
model of ISO 13790 with one thermal capacity and one resistance:

    theta_m,t = theta_m,t-1 * (1 - dt/Cm * H) + dt/Cm * (Phi_t-1 + H * theta_e,t-1)

Extended here with ventilation / infiltration losses, internal gains, solar gains through
windows and an ideal heating controller that holds a set point. Weather: one year of hourly
London data from the UK Government Home Energy Model (communitiesuk/epb-home-energy-model,
MIT), shifted per UK region.

Purpose:
* Replace fixed "fabric measure saves X %" assumptions with a physics ratio per home:
  modelled space-heating need after the measures / before. The ratio is applied to the
  home's *metered* heating fuel, so results stay anchored to real consumption.
* Size heat pumps from the design heat loss (W/K x design temperature difference).

Geometry is not collected from users, so it is estimated from floor area, property type and
build year with simple archetype rules (documented in `geometry_for`). Results are indicative.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

WEATHER_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "weather" / "london_hem.epw"

# Mean winter (Oct-Apr) temperature difference from London, degC. Applied as a constant shift.
REGION_TEMP_OFFSET_C: Dict[str, float] = {
    "London": 0.0, "South East": -0.4, "South West": -0.1, "East of England": -0.6,
    "East Midlands": -1.0, "West Midlands": -1.1, "Yorkshire and The Humber": -1.2,
    "North West": -1.0, "North East": -1.6, "Wales": -0.7, "Scotland": -2.0, "Northern Ireland": -1.3,
}

HEATING_SET_POINT_C = 20.0      # demand temperature during heating periods (living + rest of dwelling mean)
SMART_CONTROLS_SET_POINT_C = 19.4  # zoning / TRVs / scheduling lower the effective mean
DESIGN_OUTDOOR_C = -3.0         # UK design condition for heat-pump sizing (MCS-style)
DESIGN_INDOOR_C = 21.0
INTERNAL_GAINS_W_PER_M2 = 2.5
SOLAR_GAIN_FACTOR = 0.35        # g-value x frame x shading x orientation averaging
DT_S = 3600.0


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _london_weather() -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
    """(hourly dry-bulb degC, hourly solar on an average vertical window W/m2)."""
    temps: List[float] = []
    solar: List[float] = []
    with WEATHER_FILE.open(encoding="utf-8", errors="replace") as f:
        rows = list(csv.reader(f))[8:]
    for r in rows:
        temps.append(float(r[6]))
        dni = min(max(float(r[14] or 0.0), 0.0), 1000.0)  # one corrupt value in the source file
        dhi = min(max(float(r[15] or 0.0), 0.0), 800.0)
        # Average vertical plane: half the sky diffuse plus a quarter of the beam.
        solar.append(0.5 * dhi + 0.25 * dni)
    return tuple(temps), tuple(solar)


def weather_for(region: Optional[str]) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
    temps, solar = _london_weather()
    offset = REGION_TEMP_OFFSET_C.get(region or "", -0.8)  # unknown region: UK-average shift
    if offset == 0.0:
        return temps, solar
    return tuple(t + offset for t in temps), solar


# ---------------------------------------------------------------------------
# Fabric and geometry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Fabric:
    wall_u: float
    roof_u: float
    floor_u: float
    window_u: float
    infiltration_ach: float
    set_point_c: float


@dataclass(frozen=True)
class Geometry:
    floor_area_m2: float
    storeys: int
    wall_area_m2: float      # net opaque exposed wall
    window_area_m2: float
    roof_area_m2: float
    ground_floor_area_m2: float
    volume_m3: float
    thermal_capacity_j_per_k: float


def roof_u_for_depth(depth_mm: float) -> float:
    """Loft insulation depth -> U-value (RdSAP-style table, mineral wool)."""
    table = [(0, 2.3), (12, 1.5), (25, 1.0), (50, 0.68), (75, 0.5), (100, 0.4), (150, 0.3),
             (200, 0.21), (250, 0.17), (300, 0.14), (400, 0.11)]
    d = max(0.0, float(depth_mm))
    for (d0, u0), (d1, u1) in zip(table, table[1:]):
        if d <= d1:
            return u0 + (u1 - u0) * (d - d0) / (d1 - d0)
    return table[-1][1]


def floor_u_for(status: str) -> float:
    s = (status or "").lower()
    if "uninsulated" in s:
        return 0.70
    if "partial" in s:
        return 0.45
    if "insulated" in s:
        return 0.22
    return 0.60


def window_u_for(glazing: str) -> float:
    g = (glazing or "").lower()
    if "triple" in g or "passivhaus" in g:
        return 0.9
    if "high performance" in g:
        return 1.4
    if "modern" in g or "post-2002" in g:
        return 1.8
    if "secondary" in g:
        return 2.8
    if "older double" in g or "pre-2002" in g or "double" in g:
        return 2.8
    if "single" in g:
        return 4.8
    return 2.8


def infiltration_for(built_year: int) -> float:
    """Air changes per hour from infiltration, by construction era."""
    if built_year < 1919:
        return 1.0
    if built_year < 1980:
        return 0.8
    if built_year < 2006:
        return 0.6
    return 0.4


def fabric_for(prop: Mapping) -> Fabric:
    return Fabric(
        wall_u=float(prop["wall_u_value"]),
        roof_u=roof_u_for_depth(prop["roof_insulation_depth_mm"]),
        floor_u=floor_u_for(prop["floor_insulation_status"]),
        window_u=window_u_for(prop["glazing_type"]),
        infiltration_ach=infiltration_for(int(prop.get("built_year") or 1970)),
        set_point_c=SMART_CONTROLS_SET_POINT_C if prop.get("smart_controls") else HEATING_SET_POINT_C,
    )


def geometry_for(prop: Mapping) -> Geometry:
    """
    Archetype geometry from floor area and type:
    * storeys: 1 for flats, apartments and bungalows, else 2;
    * plan 1.5:1 rectangle; storey height 2.75 m before 1919, else 2.45 m;
    * exposed share of the perimeter: detached 100 %, semi / end-terrace 75 %,
      mid-terrace 50 %, flat 50 %;
    * roof and ground floor fully exposed except flats (assumed mid-floor: 0 %);
    * windows 20 % of floor area (capped at half the exposed wall);
    * thermal capacity 165 kJ/m2K of floor area (ISO 13790 "medium" class).
    """
    area = float(prop["total_floor_area_m2"])
    ptype = (prop.get("property_type") or "").lower()
    is_flat = any(k in ptype for k in ("flat", "apartment", "maisonette"))
    storeys = 1 if (is_flat or "bungalow" in ptype) else 2
    built = int(prop.get("built_year") or 1970)
    height = 2.75 if built < 1919 else 2.45

    footprint = area / storeys
    width = math.sqrt(footprint / 1.5)
    depth = 1.5 * width
    perimeter = 2 * (width + depth)
    if "detached" in ptype and "semi" not in ptype:
        exposed = 1.0
    elif "semi" in ptype or "end" in ptype:
        exposed = 0.75
    elif "terrace" in ptype or "mid" in ptype or is_flat:
        exposed = 0.5
    else:
        exposed = 0.85

    gross_wall = perimeter * exposed * height * storeys
    window = min(0.20 * area, 0.5 * gross_wall)
    return Geometry(
        floor_area_m2=area,
        storeys=storeys,
        wall_area_m2=gross_wall - window,
        window_area_m2=window,
        roof_area_m2=0.0 if is_flat else footprint,
        ground_floor_area_m2=0.0 if is_flat else footprint,
        volume_m3=area * height,
        thermal_capacity_j_per_k=165_000.0 * area,
    )


def heat_loss_w_per_k(geo: Geometry, fab: Fabric) -> Dict[str, float]:
    """Fabric (with a 0.1 W/m2K thermal-bridging allowance) and ventilation heat-loss coefficients."""
    elements = {
        "walls": fab.wall_u * geo.wall_area_m2,
        "windows": fab.window_u * geo.window_area_m2,
        "roof": fab.roof_u * geo.roof_area_m2,
        "floor": fab.floor_u * geo.ground_floor_area_m2,
    }
    exposed_area = geo.wall_area_m2 + geo.window_area_m2 + geo.roof_area_m2 + geo.ground_floor_area_m2
    elements["thermal_bridges"] = 0.1 * exposed_area
    elements["ventilation"] = 0.33 * fab.infiltration_ach * geo.volume_m3
    elements["total"] = sum(elements.values())
    return elements


# ---------------------------------------------------------------------------
# Hourly simulation (simple-simple 1R1C + gains + ideal heating control)
# ---------------------------------------------------------------------------

def heating_on(hour_of_year: int) -> bool:
    """
    UK SAP / HEM standard occupancy heating pattern: weekdays 07-09 and 16-23,
    weekends 07-23 (the weather year starts on a Wednesday).
    """
    day = hour_of_year // 24
    hour = hour_of_year % 24
    weekend = (day + 2) % 7 >= 5  # day 0 is Wednesday
    if weekend:
        return 7 <= hour < 23
    return 7 <= hour < 9 or 16 <= hour < 23


@lru_cache(maxsize=4096)
def simulate_heating_kwh(geo: Geometry, fab: Fabric, region: Optional[str]) -> float:
    """Annual space-heating need (kWh of useful heat) for one year of hourly weather. Cached."""
    temps, solar = weather_for(region)
    h = heat_loss_w_per_k(geo, fab)["total"]
    cm = geo.thermal_capacity_j_per_k
    k = DT_S / cm
    keep = 1.0 - k * h
    internal = INTERNAL_GAINS_W_PER_M2 * geo.floor_area_m2
    sol_area = SOLAR_GAIN_FACTOR * geo.window_area_m2
    set_point = fab.set_point_c

    theta = set_point
    heat_j = 0.0
    for hour, (te, sol) in enumerate(zip(temps, solar)):
        gains = internal + sol_area * sol
        free = theta * keep + k * (gains + h * te)  # temperature with no heating this hour
        if free < set_point and heating_on(hour):
            phi = (set_point - theta * keep) / k - h * te - gains  # heat that lands exactly on set point
            theta = set_point
            heat_j += phi * DT_S
        else:
            theta = free  # free-running (no cooling modelled)
    return heat_j / 3.6e6


# ---------------------------------------------------------------------------
# Measures
# ---------------------------------------------------------------------------

def apply_measures(fab: Fabric, measures: Iterable[str]) -> Fabric:
    """Fabric after retrofit measures, using the catalogue's target performance."""
    m = set(measures)
    out = fab
    if "wall_insulation" in m:
        out = replace(out, wall_u=min(out.wall_u, 0.30))
    if "loft_insulation" in m:
        out = replace(out, roof_u=min(out.roof_u, roof_u_for_depth(300)))
    if "floor_insulation" in m:
        out = replace(out, floor_u=min(out.floor_u, 0.22))
    if "window_upgrade" in m:
        out = replace(out, window_u=min(out.window_u, 1.4))
    if "draught_proofing" in m:
        out = replace(out, infiltration_ach=out.infiltration_ach * 0.65)
    if "smart_controls" in m:
        out = replace(out, set_point_c=min(out.set_point_c, SMART_CONTROLS_SET_POINT_C))
    return out


FABRIC_MEASURES = ("wall_insulation", "loft_insulation", "floor_insulation", "window_upgrade",
                   "draught_proofing", "smart_controls")


# Household heating behaviour: in poor homes people heat fewer rooms for fewer hours than the
# model assumes ("prebound effect"), and after a retrofit part of the saving is taken back as
# extra warmth. Modelled savings are therefore scaled by metered / modelled heat, clamped.
PREBOUND_MIN, PREBOUND_MAX, PREBOUND_DEFAULT = 0.5, 1.0, 0.7
SPACE_HEATING_SHARE_OF_GAS = 0.90  # same split the carbon engine uses


def prebound_ratio(prop: Mapping, modelled_heat_kwh: float) -> Tuple[float, bool]:
    """(ratio, calibrated?) Metered useful space heat / modelled need, clamped to [0.5, 1]."""
    gas = float(prop.get("annual_gas_kwh") or 0.0)
    if gas <= 0 or modelled_heat_kwh <= 0:
        return PREBOUND_DEFAULT, False
    eff = min(float(prop.get("heating_efficiency_pct") or 85.0), 100.0) / 100.0
    metered_heat = gas * SPACE_HEATING_SHARE_OF_GAS * eff
    return min(PREBOUND_MAX, max(PREBOUND_MIN, metered_heat / modelled_heat_kwh)), True


@dataclass(frozen=True)
class PhysicsResult:
    heat_loss_w_per_k: float
    heat_loss_breakdown: Dict[str, float]
    design_heat_loss_kw: float
    heating_need_before_kwh: float
    heating_need_after_kwh: float
    fabric_reduction_factor: float          # after / before, applied to metered heating fuel
    heat_loss_after_w_per_k: float
    design_heat_loss_after_kw: float
    recommended_heat_pump_kw: float
    per_measure_saving_pct: Dict[str, float]
    prebound_ratio: float = 1.0                # metered / modelled heat (clamped 0.5-1)
    calibrated_to_meter: bool = False
    realised_reduction_factor: float = 1.0      # what the carbon engine applies to metered fuel
    model: str = "simple-simple 1R1C hourly (ISO 13790 derived) + HEM London weather"


def assess_physics(prop: Mapping, measures: Iterable[str], per_measure: bool = True) -> PhysicsResult:
    measures = [m for m in measures if m in FABRIC_MEASURES]
    region = prop.get("region")
    geo = geometry_for(prop)
    fab = fabric_for(prop)
    after = apply_measures(fab, measures)

    before_kwh = simulate_heating_kwh(geo, fab, region)
    after_kwh = simulate_heating_kwh(geo, after, region) if measures else before_kwh
    hl = heat_loss_w_per_k(geo, fab)
    hl_after = heat_loss_w_per_k(geo, after)

    breakdown: Dict[str, float] = {}
    for m in (measures if per_measure else []):
        alone = simulate_heating_kwh(geo, apply_measures(fab, [m]), region)
        breakdown[m] = round(100.0 * (1.0 - alone / before_kwh), 1) if before_kwh > 0 else 0.0

    design_after_kw = hl_after["total"] * (DESIGN_INDOOR_C - DESIGN_OUTDOOR_C) / 1000.0
    physics_factor = after_kwh / before_kwh if before_kwh > 0 else 1.0
    ratio, calibrated = prebound_ratio(prop, before_kwh)
    realised = 1.0 - (1.0 - physics_factor) * ratio
    return PhysicsResult(
        heat_loss_w_per_k=round(hl["total"], 1),
        heat_loss_breakdown={k: round(v, 1) for k, v in hl.items() if k != "total"},
        design_heat_loss_kw=round(hl["total"] * (DESIGN_INDOOR_C - DESIGN_OUTDOOR_C) / 1000.0, 2),
        heating_need_before_kwh=round(before_kwh, 0),
        heating_need_after_kwh=round(after_kwh, 0),
        fabric_reduction_factor=round(physics_factor, 4),
        heat_loss_after_w_per_k=round(hl_after["total"], 1),
        design_heat_loss_after_kw=round(design_after_kw, 2),
        # Heat pumps are sold in whole kW; size to the post-retrofit design load.
        recommended_heat_pump_kw=float(max(3, math.ceil(design_after_kw))),
        per_measure_saving_pct=breakdown,
        prebound_ratio=round(ratio, 3),
        calibrated_to_meter=calibrated,
        realised_reduction_factor=round(realised, 4),
    )

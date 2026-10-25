"""
Detailed hourly simulation to EN ISO 52016-1 with pyBuildingEnergy (EURAC-EEBgroup, BSD-3).

The home is described with the same archetype geometry and fabric as the fast model
(backend.engine.thermal_model), split into four orientated facades, a roof and a ground slab,
then simulated hour by hour with the HEM London weather file (shifted per region). The result
is the annual space-heating need before and after the selected fabric measures.

Runs in the Linux physics container: on Windows hosts with Smart App Control, pvlib's h5py
dependency is blocked from loading, so the fast model remains the default everywhere.
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from backend.engine import thermal_model as tm

# Approximate population-weighted centroids (lat, lon) used for solar geometry.
REGION_LATLON: Dict[str, Tuple[float, float]] = {
    "London": (51.51, -0.13), "South East": (51.27, -0.75), "South West": (50.95, -3.2),
    "East of England": (52.2, 0.4), "East Midlands": (52.9, -1.1), "West Midlands": (52.48, -1.9),
    "Yorkshire and The Humber": (53.8, -1.5), "North West": (53.5, -2.5), "North East": (54.97, -1.6),
    "Wales": (51.8, -3.5), "Scotland": (55.9, -3.6), "Northern Ireland": (54.6, -6.0),
}
# Share of window area per facade (UK homes favour front/back, i.e. north/south).
WINDOW_SHARE = {"S": 0.40, "N": 0.30, "E": 0.15, "W": 0.15}
AZIMUTH = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}
# Thermal capacities per element (J/K), magnitudes from the pyBuildingEnergy reference example.
CAPACITY = {"wall": 1_416_240.0, "roof": 741_500.0, "slab": 405_801.0}


def _profile(weekend: bool) -> List[float]:
    """SAP / HEM heating pattern as 24 hourly on/off values."""
    if weekend:
        return [1.0 if 7 <= h < 23 else 0.0 for h in range(24)]
    return [1.0 if (7 <= h < 9 or 16 <= h < 23) else 0.0 for h in range(24)]


def _surface(name, kind, area, u, azimuth, tilt, capacity=0.0, ground=False, **extra) -> Dict[str, Any]:
    # pyBuildingEnergy finds the slab-on-ground by sky_view_factor == 0 (ISO 13370 ground model);
    # roofs see the whole sky (1.0) and walls half of it (0.5).
    sky = 0.0 if ground else (1.0 if tilt == 0 else 0.5)
    s = {
        "name": name, "type": kind, "area": round(float(area), 3), "sky_view_factor": sky,
        "u_value": round(float(u), 4), "solar_absorptance": 0.6, "thermal_capacity": capacity,
        "orientation": {"azimuth": azimuth, "tilt": tilt}, "name_adj_zone": None,
        "height": 2.5, "length": max(0.1, round(float(area) / 2.5, 3)),
    }
    s.update(extra)
    return s


def build_bui(prop: Mapping, fabric: "tm.Fabric") -> Dict[str, Any]:
    """pyBuildingEnergy building object for one of our homes (pure data; no library import)."""
    geo = tm.geometry_for(prop)
    region = prop.get("region") or "London"
    lat, lon = REGION_LATLON.get(region, REGION_LATLON["London"])
    storey_h = geo.volume_m3 / geo.floor_area_m2
    footprint = geo.floor_area_m2 / geo.storeys
    width = (footprint / 1.5) ** 0.5
    depth = 1.5 * width
    side_len = {"N": width, "S": width, "E": depth, "W": depth}
    gross_wall_total = geo.wall_area_m2 + geo.window_area_m2
    perimeter = 2 * (width + depth)

    surfaces: List[Dict[str, Any]] = []
    if geo.roof_area_m2 > 0:
        surfaces.append(_surface("Roof", "opaque", geo.roof_area_m2, fabric.roof_u, 0.0, 0.0, CAPACITY["roof"]))
    for side in ("N", "E", "S", "W"):
        gross = gross_wall_total * side_len[side] / perimeter
        win = geo.window_area_m2 * WINDOW_SHARE[side]
        opaque = max(0.1, gross - win)
        surfaces.append(_surface(f"Wall {side}", "opaque", opaque, fabric.wall_u, AZIMUTH[side], 90.0, CAPACITY["wall"]))
        if win > 0:
            surfaces.append(_surface(
                f"Window {side}", "transparent", win, fabric.window_u, AZIMUTH[side], 90.0, 0.0,
                g_value=0.63, width=1.2, parapet=0.9, shading=False, shading_type="horizontal_overhang",
                width_or_distance_of_shading_elements=0.0, overhang_properties={"width_of_horizontal_overhangs": 0.0},
            ))
    if geo.ground_floor_area_m2 > 0:
        surfaces.append(_surface("Ground floor", "opaque", geo.ground_floor_area_m2, fabric.floor_u, 0.0, 0.0, CAPACITY["slab"], ground=True))

    exposed_area = geo.wall_area_m2 + geo.window_area_m2 + geo.roof_area_m2 + geo.ground_floor_area_m2
    h_ve = 0.33 * fabric.infiltration_ach * geo.volume_m3
    always = [1.0] * 24
    return {
        "building": {
            "name": f"Eco {prop.get('property_id', 'home')}",
            "azimuth_relative_to_true_north": 0,
            "latitude": lat, "longitude": lon,
            "exposed_perimeter": round(perimeter, 3),
            "height": round(storey_h * geo.storeys, 3),
            "wall_thickness": 0.3,
            "n_floors": geo.storeys,
            "building_type_class": "Residential_apartment",
            "adj_zones_present": False, "number_adj_zone": 0,
            "net_floor_area": geo.floor_area_m2,
            "construction_class": "class_i",
            "construction_year": "2010-today",
            "country": "United Kingdom",
        },
        "adjacent_zones": [],
        "building_surface": surfaces,
        "units": {"area": "m2", "u_value": "W/m2K", "thermal_capacity": "J/K", "azimuth": "degrees", "tilt": "degrees"},
        "building_parameters": {
            "temperature_setpoints": {
                "heating_setpoint": fabric.set_point_c, "heating_setback": 12.0,
                "cooling_setpoint": 40.0, "cooling_setback": 45.0,  # UK homes: no active cooling
                "units": "C",
            },
            "system_capacities": {"heating_capacity": 1.0e7, "cooling_capacity": 1.0e7, "units": "W"},
            "ventilation": {
                "ventilation_type": "custom", "flow_rate_per_person": 0.0, "units": "l/(s m2)",
                "custom_heat_transfer_coefficient_ventilation": round(h_ve, 3),
            },
            "internal_gains": [
                {"name": "occupants_and_appliances", "full_load": tm.INTERNAL_GAINS_W_PER_M2,
                 "weekday": always, "weekend": always},
            ],
            "construction": {"wall_thickness": 0.3, "thermal_bridge_heat_W_K": round(0.1 * exposed_area, 3)},
            "climate_parameters": {"coldest_month": 1},
            "heating_profile": {"weekday": _profile(False), "weekend": _profile(True)},
            "cooling_profile": {"weekday": [0.0] * 24, "weekend": [0.0] * 24},
            "ventilation_profile": {"weekday": always, "weekend": always},
        },
    }


def regional_epw(region: str) -> Path:
    """The HEM London EPW with the dry-bulb column shifted for the region (cached in temp)."""
    offset = tm.REGION_TEMP_OFFSET_C.get(region or "", -0.8)
    if offset == 0.0:
        return tm.WEATHER_FILE
    out = Path(tempfile.gettempdir()) / f"eco_hem_{region.replace(' ', '_')}.epw"
    if not out.exists():
        rows = list(csv.reader(tm.WEATHER_FILE.open(encoding="utf-8", errors="replace")))
        for r in rows[8:]:
            r[6] = f"{float(r[6]) + offset:.1f}"
        with out.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
    return out


def _annual_heating_kwh(result) -> float:
    """Accepts the 2- or 3-tuple return of pyBuildingEnergy 2.0.x and reads the heating need."""
    hourly, annual = result[0], result[1]
    for col in ("Q_H_annual", "Q_H_annual_Wh"):
        if hasattr(annual, "columns") and col in annual.columns:
            return float(annual[col].iloc[0]) / 1000.0
    if hasattr(annual, "columns") and "Q_H_annual_kWh" in annual.columns:
        return float(annual["Q_H_annual_kWh"].iloc[0])
    return float(hourly["Q_H"].clip(lower=0).sum()) / 1000.0  # hourly W over 1 h steps -> Wh


def simulate_detailed(prop: Mapping, measures: List[str]) -> Dict[str, Any]:
    import pybuildingenergy as pb  # heavy; only imported in the physics container

    try:
        from pybuildingenergy.source.check_input import sanitize_and_validate_BUI
    except ImportError:  # older releases
        sanitize_and_validate_BUI = None

    fab = tm.fabric_for(prop)
    after = tm.apply_measures(fab, [m for m in measures if m in tm.FABRIC_MEASURES])
    epw = str(regional_epw(prop.get("region") or "London"))

    def run(fabric) -> float:
        bui = build_bui(prop, fabric)
        if sanitize_and_validate_BUI is not None:
            bui, _ = sanitize_and_validate_BUI(bui, fix=True)
        out = pb.ISO52016.Temperature_and_Energy_needs_calculation(bui, weather_source="epw", path_weather_file=epw)
        return _annual_heating_kwh(out)

    before = run(fab)
    after_kwh = run(after) if measures else before
    fast = tm.assess_physics(prop, measures, per_measure=False)
    return {
        "model": "EN ISO 52016-1 hourly (pyBuildingEnergy) + HEM London weather",
        "heating_need_before_kwh": round(before, 0),
        "heating_need_after_kwh": round(after_kwh, 0),
        "fabric_reduction_factor": round(after_kwh / before, 4) if before > 0 else 1.0,
        "fast_model": {
            "heating_need_before_kwh": fast.heating_need_before_kwh,
            "heating_need_after_kwh": fast.heating_need_after_kwh,
            "fabric_reduction_factor": fast.fabric_reduction_factor,
        },
        "agreement_pct": round(100.0 * (1 - abs(fast.heating_need_before_kwh - before) / before), 1) if before > 0 else None,
    }

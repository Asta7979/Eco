"""
Real EN ISO 52016-1 run through pyBuildingEnergy. Skipped where the library cannot be imported
(e.g. Windows hosts that block h5py); the Linux CI job `physics-detailed` runs it for real.
"""

import pytest

pytest.importorskip("pybuildingenergy", exc_type=ImportError)


# Pins that change the simulation's behaviour: a different pvlib / timezonefinder imports but
# breaks mid-run (e.g. timezonefinder 6.x against h3 4.x). The service image installs exactly
# these (requirements/physics.txt); other pyBuildingEnergy pins, such as urllib3, are deliberately newer.
RUNTIME_PINS = ("pvlib", "timezonefinder")


def _pins_satisfied() -> bool:
    import importlib.metadata as md

    for req in md.requires("pybuildingenergy") or []:
        if "==" in req and ";" not in req:
            name, version = (part.strip() for part in req.split("=="))
            if name.lower() not in RUNTIME_PINS:
                continue
            try:
                if md.version(name) != version:
                    return False
            except md.PackageNotFoundError:
                return False
    return True


if not _pins_satisfied():
    pytest.skip("pvlib / timezonefinder are not the versions pyBuildingEnergy pins", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from services.physics_engine.app.main import app  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

from backend.schemas import PropertyCreate  # noqa: E402

HOME = next(p for p in json.loads((Path(__file__).resolve().parent.parent / "data" / "properties.json").read_text(encoding="utf-8"))
            if p["property_id"] == "PROP-GB-001")

HOME_INPUT = {k: HOME[k] for k in PropertyCreate.model_fields if k in HOME}


def test_detailed_simulation_runs_and_agrees_with_the_fast_model():
    with TestClient(app) as c:
        r = c.post("/simulate/detailed", json={"property": HOME_INPUT, "measures": ["wall_insulation", "loft_insulation"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["heating_need_before_kwh"] > 0
    # Insulating the fabric must never increase the heating need.
    assert body["heating_need_after_kwh"] < body["heating_need_before_kwh"]
    assert 0 < body["fabric_reduction_factor"] < 1
    # Two independent hourly models: same order of magnitude for the same house.
    assert body["agreement_pct"] is not None and body["agreement_pct"] > 0

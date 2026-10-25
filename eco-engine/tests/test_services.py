"""
Microservice architecture tests: the vision, LLM and physics services on their own, the
ISO 52016 building builder, the gateway routing to the services (via in-memory test clients)
and the /api/v1 contracts.
"""

import importlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.engine import thermal_model as tm
from backend.services import remote

PROPS = {p["property_id"]: p for p in json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "properties.json").read_text(encoding="utf-8"))}
HOME = PROPS["PROP-GB-001"]
HOME_INPUT = {k: HOME[k] for k in (
    "address", "postcode", "region", "property_type", "built_year", "total_floor_area_m2", "baseline_value_gbp",
    "current_epc_score", "wall_type", "wall_insulation_status", "wall_u_value", "roof_type",
    "roof_insulation_depth_mm", "floor_type", "floor_insulation_status", "glazing_type", "heating_type",
    "heating_efficiency_pct", "annual_gas_kwh", "annual_electricity_kwh", "solar_pv_kwp",
    "battery_storage_kwh", "smart_controls")}


def thermal_jpeg():
    t = np.full((300, 400), 40, np.uint8)
    cv2.rectangle(t, (150, 120), (230, 190), 230, -1)
    ok, buf = cv2.imencode(".jpg", cv2.applyColorMap(cv2.GaussianBlur(t, (0, 0), 3), cv2.COLORMAP_INFERNO))
    return buf.tobytes()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ECO_VISION_WARMUP", "0")
    monkeypatch.delenv("ECO_SERVICE_TOKEN", raising=False)
    for env in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OLLAMA_HOST", "ECO_LLM_PROVIDER", "ECO_LLM_ENABLED"):
        monkeypatch.delenv(env, raising=False)
    for env in ("VISION_SERVICE_URL", "LLM_SERVICE_URL", "PHYSICS_SERVICE_URL"):
        monkeypatch.delenv(env, raising=False)
        remote.set_test_client(env, None)
    yield
    for env in ("VISION_SERVICE_URL", "LLM_SERVICE_URL", "PHYSICS_SERVICE_URL"):
        remote.set_test_client(env, None)


@pytest.fixture
def vision_app():
    from services.vision_service.app.main import app
    return app


@pytest.fixture
def llm_app():
    from services.llm_service.app.main import app
    return app


@pytest.fixture
def physics_app():
    from services.physics_engine.app.main import app
    return app


# ---------- Service token ----------

def test_internal_endpoints_require_the_service_token(monkeypatch, physics_app):
    monkeypatch.setenv("ECO_SERVICE_TOKEN", "t" * 40)
    with TestClient(physics_app) as c:
        body = {"property": HOME_INPUT, "measures": ["loft_insulation"]}
        assert c.post("/simulate", json=body).status_code == 401
        assert c.post("/simulate", json=body, headers={"X-Service-Token": "wrong"}).status_code == 401
        assert c.post("/simulate", json=body, headers={"X-Service-Token": "t" * 40}).status_code == 200
        assert c.get("/health").status_code == 200  # liveness stays open for the orchestrator


def test_services_refuse_to_start_in_production_without_a_strong_token(monkeypatch):
    from services.common.app_factory import create_service_app

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ECO_SERVICE_TOKEN", "short")
    with pytest.raises(RuntimeError):
        create_service_app("x", "y")


# ---------- Vision service ----------

def test_vision_detect_contract(vision_app):
    with TestClient(vision_app) as c:
        r = c.post("/detect", files={"file": ("t.jpg", thermal_jpeg(), "image/jpeg")}, data={"mode": "thermal"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "thermal" and body["image_stored"] is False
    assert body["glazing_type"]["predicted"] is None and "not inferred" in body["glazing_type"]["note"]
    for d in body["detections"]:
        x1, y1, x2, y2 = d["box"]
        assert 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1


def test_vision_rejects_bad_input(vision_app):
    with TestClient(vision_app) as c:
        assert c.post("/detect", files={"file": ("x.jpg", b"not an image", "image/jpeg")}, data={"mode": "auto"}).status_code == 422
        assert c.post("/segment", files={"file": ("x.jpg", b"x", "image/jpeg")}, data={"boxes": "nope"}).status_code == 422


@pytest.mark.parametrize("boxes", [[None], [1], ["abcd"], [[0, 0, None, 1]], [[False, 0, 1, 1]]])
def test_segment_routes_reject_malformed_coordinates(vision_app, boxes):
    from fastapi import FastAPI
    from backend.api.routes.vision import router

    gateway = FastAPI()
    gateway.include_router(router)
    for app, path in ((gateway, "/vision/segment"), (vision_app, "/segment")):
        with TestClient(app) as c:
            response = c.post(
                path, files={"file": ("x.jpg", b"x", "image/jpeg")},
                data={"boxes": json.dumps(boxes)},
            )
        assert response.status_code == 422


# ---------- LLM service ----------

def test_llm_parse_returns_only_catalogue_codes(llm_app):
    with TestClient(llm_app) as c:
        r = c.post("/parse", json={"user_query": "What if I add a heat pump and loft insulation with a £12k budget?",
                                   "default_property_id": "PROP-ABC12345"})
    assert r.status_code == 200
    body = r.json()
    assert {"heat_pump", "loft_insulation"} <= set(body["requested_interventions"])
    assert body["budget_constraint_gbp"] == 12000


def test_llm_explain_falls_back_to_template_without_a_key(llm_app):
    with TestClient(llm_app) as c:
        r = c.post("/explain", json={"metrics": {"saving": 737}, "user_query": "bills?", "template_text": "TEMPLATE"})
    assert r.json() == {"text": "TEMPLATE", "source": "template", "numeric_grounding_passed": True}


def test_llm_guardrails():
    from pydantic import ValidationError
    from services.llm_service.app.guardrails import ParsedIntent, grounded

    assert grounded("Save **£737** a year.", {"net_bill_savings_gbp": 737.24}, "bills?")
    assert not grounded("Save **£950** a year.", {"net_bill_savings_gbp": 737.24}, "bills?")
    with pytest.raises(ValidationError):
        ParsedIntent(detected_property_id="P", requested_interventions=["rm_rf_everything"], user_primary_goal="bill_savings")


# ---------- Physics service ----------

def test_physics_simulate_contract(physics_app):
    with TestClient(physics_app) as c:
        r = c.post("/simulate", json={"property": HOME_INPUT, "measures": ["wall_insulation", "loft_insulation"]})
    assert r.status_code == 200
    body = r.json()
    assert body["heating_need_after_kwh"] < body["heating_need_before_kwh"]
    assert body["carbon_factors"]["gas"] == 0.18316 and body["carbon_factors"]["electricity"] == 0.20705
    assert body["recommended_heat_pump_kw"] >= 3


def test_physics_validates_property_input(physics_app):
    with TestClient(physics_app) as c:
        assert c.post("/simulate", json={"property": {**HOME_INPUT, "total_floor_area_m2": -1}}).status_code == 422
        assert c.post("/simulate", json={"property": HOME_INPUT, "measures": ["<script>"]}).status_code == 422


def test_iso52016_building_matches_the_fast_model_geometry():
    from services.physics_engine.app.iso52016 import WINDOW_SHARE, build_bui

    fab = tm.fabric_for(HOME)
    geo = tm.geometry_for(HOME)
    bui = build_bui(HOME, fab)
    surfaces = bui["building_surface"]
    walls = [s for s in surfaces if s["name"].startswith("Wall")]
    windows = [s for s in surfaces if s["type"] == "transparent"]
    assert sum(s["area"] for s in walls) + sum(s["area"] for s in windows) == pytest.approx(geo.wall_area_m2 + geo.window_area_m2, rel=1e-3)
    assert sum(s["area"] for s in windows) == pytest.approx(geo.window_area_m2 * sum(WINDOW_SHARE.values()), rel=1e-3)
    assert all(s["u_value"] == pytest.approx(fab.wall_u, abs=1e-4) for s in walls)
    assert {s["orientation"]["azimuth"] for s in walls} == {0.0, 90.0, 180.0, 270.0}
    ground = [s for s in surfaces if s["sky_view_factor"] == 0]  # how pyBuildingEnergy finds the slab
    assert [s["name"] for s in ground] == ["Ground floor"]
    assert bui["building_parameters"]["temperature_setpoints"]["heating_setpoint"] == fab.set_point_c
    assert sum(bui["building_parameters"]["heating_profile"]["weekday"]) == 9  # 07-09 and 16-23

    insulated = build_bui(HOME, tm.apply_measures(fab, ["wall_insulation"]))
    assert all(s["u_value"] <= 0.30 for s in insulated["building_surface"] if s["name"].startswith("Wall"))


def test_regional_weather_file_is_shifted(tmp_path, monkeypatch):
    import csv
    import tempfile

    from services.physics_engine.app.iso52016 import regional_epw

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    scot = regional_epw("Scotland")
    base = list(csv.reader(tm.WEATHER_FILE.open(encoding="utf-8", errors="replace")))[8]
    shifted = list(csv.reader(scot.open(encoding="utf-8")))[8]
    assert float(shifted[6]) == pytest.approx(float(base[6]) + tm.REGION_TEMP_OFFSET_C["Scotland"], abs=0.05)


# ---------- Gateway routing ----------

@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'gw.db'}")
    monkeypatch.setenv("ECO_AUTH_SECRET", "services-test-secret-at-least-32-chars")
    monkeypatch.setenv("ECO_RATE_LIMIT", "0")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    import backend.db.database as database
    import backend.main as main

    importlib.reload(database)
    importlib.reload(main)
    with TestClient(main.app) as c:
        main.app.dependency_overrides.clear()
        token = c.post("/auth/register", json={"email": "svc@example.com", "password": "strong-pass-1"}).json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        pid = c.post("/properties", json=HOME_INPUT).json()["property_id"]
        yield c, pid


def test_gateway_routes_vision_to_the_service(gateway, vision_app):
    c, _ = gateway
    remote.set_test_client("VISION_SERVICE_URL", TestClient(vision_app))
    r = c.post("/vision/analyze", files={"file": ("t.jpg", thermal_jpeg(), "image/jpeg")}, data={"mode": "thermal"})
    assert r.status_code == 200 and "glazing_type" in r.json()  # only the service adds this field
    v1 = c.post("/api/v1/cv", files={"file": ("t.jpg", thermal_jpeg(), "image/jpeg")}, data={"mode": "thermal"})
    assert v1.status_code == 200 and v1.json()["mode"] == "thermal"


def test_versioned_vision_accepts_normal_photo_size(gateway, vision_app):
    c, _ = gateway
    remote.set_test_client("VISION_SERVICE_URL", TestClient(vision_app))
    photo = np.random.default_rng(0).integers(0, 256, (600, 600, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", photo)
    assert ok and len(encoded) > 256 * 1024
    response = c.post("/api/v1/cv", files={"file": ("photo.jpg", encoded.tobytes(), "image/jpeg")}, data={"mode": "thermal"})
    assert response.status_code == 200


def test_gateway_revalidates_llm_service_replies(gateway, monkeypatch, llm_app):
    from services.llm_service.app import scenario_parser
    from services.llm_service.app.guardrails import ExplainResponse

    c, pid = gateway
    remote.set_test_client("LLM_SERVICE_URL", TestClient(llm_app))
    # A faulty LLM service claims an invented figure passed its checks.
    monkeypatch.setattr(scenario_parser, "explain",
                        lambda m, q, t: ExplainResponse(text="You will save £999,999 a year.", source="llm", numeric_grounding_passed=True))
    r = c.post("/api/v1/scenario", json={"user_query": "What if I add a heat pump?", "property_id": pid})
    assert r.status_code == 200
    assert "999,999" not in r.json()["natural_language_explanation"]
    assert r.json()["explanation_source"] == "template"


def test_v1_contracts(gateway):
    c, pid = gateway
    retro = c.post("/api/v1/retrofit", json={"property_id": pid, "selected_interventions": ["heat_pump"]})
    assert retro.status_code == 200
    body = retro.json()
    assert body["carbon_impact"]["delta_gas_kwh"] < 0 < body["carbon_impact"]["delta_electricity_kwh"]
    assert body["physics"]["heat_loss_w_per_k"] > 0
    score = c.post("/api/v1/score", json={"epc_score": 50, "primary_energy_kwh_m2": 250, "wall_u_value": 1.5,
                                           "roof_insulation_depth_mm": 100, "heating_type": "Gas Boiler (Condensing)",
                                           "heating_efficiency_pct": 88, "glazing_type": "Double"})
    assert score.status_code == 200 and 0 <= score.json()["headline_score"] <= 100
    phys = c.get(f"/api/v1/physics/{pid}")
    assert phys.status_code == 200 and phys.json()["per_measure_saving_pct"]
    assert c.post(f"/api/v1/physics/{pid}/detailed", json={"measures": []}).status_code == 503  # no physics service here


def test_v1_requires_sign_in(gateway):
    c, pid = gateway
    c.headers.pop("Authorization")
    assert c.post("/api/v1/retrofit", json={"property_id": pid, "selected_interventions": []}).status_code == 401
    assert c.get(f"/api/v1/physics/{pid}").status_code == 401


def test_unknown_measure_codes_are_rejected_not_dropped():
    from pydantic import ValidationError

    from backend.schemas import CarbonEvaluationRequest, DAGSolveRequest, FullRetrofitRequest

    with pytest.raises(ValidationError, match="air_source_heat_pump"):
        FullRetrofitRequest(property_id="PROP-GB-001", selected_interventions=["air_source_heat_pump"])
    with pytest.raises(ValidationError):
        DAGSolveRequest(requested_interventions=["heat_pump", "solar_panels"])
    with pytest.raises(ValidationError, match="solar_panels"):
        DAGSolveRequest(requested_interventions=[], current_installed_actions=["solar_panels"])
    with pytest.raises(ValidationError, match="solar_panels"):
        CarbonEvaluationRequest(
            baseline_gas_kwh=12000, baseline_electricity_kwh=3000,
            property_floor_area_m2=90, interventions=["solar_panels"],
        )
    assert FullRetrofitRequest(property_id="PROP-GB-001", selected_interventions=["heat_pump"]).selected_interventions == ["heat_pump"]


def test_live_camera_frames_use_the_fast_path(gateway, vision_app, monkeypatch):
    from backend.engine import vision_engine as ve

    c, _ = gateway
    calls = []
    monkeypatch.setattr(ve.yolo_world, "detect", lambda img: calls.append("yolo-world"))
    frame = cv2.imencode(".jpg", np.full((480, 640, 3), 128, np.uint8))[1].tobytes()
    for routed in (False, True):  # in-process, then through the vision service
        if routed:
            remote.set_test_client("VISION_SERVICE_URL", TestClient(vision_app))
        r = c.post("/vision/live", files={"file": ("f.jpg", frame, "image/jpeg")}, data={"mode": "exterior"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["live"] is True and body["segmentable"] == []
        assert "opencv-geometry" not in body["engines_used"] and "opencv-pv-texture" not in body["engines_used"]
    assert calls == []  # the slow open-vocabulary model never runs on live frames

    too_big = b"\xff\xd8" + b"0" * (ve.MAX_LIVE_BYTES + 10)
    assert c.post("/vision/live", files={"file": ("f.jpg", too_big, "image/jpeg")}, data={"mode": "exterior"}).status_code == 413


def test_live_camera_has_its_own_rate_limit():
    from backend.core.security import RATE_RULES

    rules = {(m, p): lim for m, p, lim in RATE_RULES}
    assert ("POST", "/vision/live") in rules
    assert rules[("POST", "/vision/live")] is not rules[("POST", "/vision/analyze")]

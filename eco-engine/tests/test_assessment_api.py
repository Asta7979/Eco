"""
Tests for the shared assessment pipeline, FR2 ranking, the guarded explainer's
numeric grounding check, intent parsing, and the REST endpoints.
"""

import json
from pathlib import Path

import pytest

from backend.engine.assessment import (
    WHOLE_HOME_PACKAGE,
    baseline_score_request,
    improvement_potential,
    score_drivers,
)
from backend.engine.score_engine import calculate_green_home_score
from backend.engine import llm_explainer
from backend.engine.llm_guardrails import GuardedScenarioOrchestrator

PROPERTIES = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "properties.json").read_text(encoding="utf-8")
)
BY_ID = {p["property_id"]: p for p in PROPERTIES}


def test_uninsulated_floor_is_not_counted_as_insulated():
    """'Uninsulated' contains the substring 'insulated'; it must still score as uninsulated."""
    prop = BY_ID["PROP-GB-001"]
    assert prop["floor_insulation_status"] == "Uninsulated"
    assert baseline_score_request(prop).floor_insulated is False


def test_improvement_potential_matches_formula_and_splits_fuels():
    pot = improvement_potential(BY_ID["PROP-GB-001"])
    e_base, e_after = pot["baseline_energy_kwh"], pot["after_energy_kwh"]
    assert pot["improvement_potential_pct"] == round(100 * (e_base - e_after) / e_base, 1)
    # Heat pump retrofit removes gas; gas and electricity are reported separately.
    assert pot["delta_gas_kwh"] < 0
    assert pot["delta_gas_kwh"] != pot["delta_electricity_kwh"]


def test_improvement_potential_unavailable_when_baseline_is_zero():
    prop = dict(BY_ID["PROP-GB-001"], annual_gas_kwh=0.0, annual_electricity_kwh=0.0)
    assert improvement_potential(prop)["improvement_potential_pct"] is None


def test_drivers_name_weak_components_worst_first():
    score = calculate_green_home_score(baseline_score_request(BY_ID["PROP-GB-002"]))
    drivers = score_drivers(score)
    assert 1 <= len(drivers) <= 3
    lost = {c.name: c.weight * (100 - c.sub_score) for c in score.components}
    assert [lost[d] for d in drivers] == sorted((lost[d] for d in drivers), reverse=True)


def test_efficient_home_has_no_drivers():
    score = calculate_green_home_score(baseline_score_request(BY_ID["PROP-GB-020"]))
    assert score_drivers(score) == []


# ---------- Guarded explainer: numeric grounding ----------

def test_grounding_accepts_only_audited_numbers():
    allowed = llm_explainer.allowed_numbers({"saving": 737.24, "co2_t": 3.0, "delta_gas": -18200.0})
    assert llm_explainer.ungrounded_numbers("Save **£737** a year and cut 18,200 kWh of gas (3.00 t).", allowed) == []
    assert llm_explainer.ungrounded_numbers("You could save £900 a year.", allowed) == ["900"]


def test_grounding_ignores_digits_inside_words():
    allowed = llm_explainer.allowed_numbers({"x": 1.0})
    assert llm_explainer.ungrounded_numbers("Cuts CO2 and kWh/m2 use.", allowed) == []


def test_explainer_uses_template_without_credentials(monkeypatch):
    for env in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OLLAMA_HOST", "ECO_LLM_PROVIDER", "ECO_LLM_ENABLED"):
        monkeypatch.delenv(env, raising=False)
    text, source, ok = llm_explainer.explain({"a": 1}, "q", "TEMPLATE")
    assert (text, source, ok) == ("TEMPLATE", "template", True)


# ---------- Intent parsing ----------

@pytest.mark.parametrize(
    "query, codes, budget",
    [
        ("What if I install a heat pump with a £15,000 budget?", ["heat_pump"], 15000.0),
        ("Every evaluation of solar panels", ["solar_pv"], None),
        ("I have 12k pounds for an EV charger and loft", ["ev_charger", "loft_insulation"], 12000.0),
    ],
)
def test_intent_extraction(query, codes, budget):
    intent = GuardedScenarioOrchestrator(None).extract_intent(query, "PROP-GB-001")
    assert sorted(intent.requested_interventions) == sorted(codes)
    assert intent.budget_constraint_gbp == budget


# ---------- REST API ----------

from backend.auth import AuthUser  # noqa: E402

TEST_OWNER = AuthUser("owner-test", "owner@example.com", "Test Owner")
OTHER_USER = AuthUser("other-test", "other@example.com", "Other User")


def _seed_owned_properties(database, owner_uid):
    """Test fixture data only: the app itself never seeds demo homes."""
    db = database.SessionLocal()
    try:
        cols = {c.name for c in database.PropertyModel.__table__.columns}
        for r in PROPERTIES:
            db.add(database.PropertyModel(owner_uid=owner_uid, **{k: v for k, v in r.items() if k in cols}))
        db.commit()
    finally:
        db.close()


def as_user(user):
    import backend.main as main
    from backend.auth import get_current_user

    main.app.dependency_overrides[get_current_user] = lambda: user

@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module):
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    monkeypatch_module.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    for env in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OLLAMA_HOST", "ECO_LLM_PROVIDER", "ECO_LLM_ENABLED"):
        monkeypatch_module.delenv(env, raising=False)
    monkeypatch_module.delenv("SUPABASE_URL", raising=False)
    monkeypatch_module.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch_module.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch_module.setenv("ECO_VISION_WARMUP", "0")
    monkeypatch_module.setenv("ECO_RATE_LIMIT", "0")  # rate limits have their own tests
    monkeypatch_module.setenv("ECO_AUTH_SECRET", "test-secret-not-for-production-use-only")

    import importlib
    import backend.db.database as database
    import backend.main as main
    importlib.reload(database)
    importlib.reload(main)

    from fastapi.testclient import TestClient
    from backend.auth import get_current_user

    with TestClient(main.app) as c:
        _seed_owned_properties(database, TEST_OWNER.id)
        main.app.dependency_overrides[get_current_user] = lambda: TEST_OWNER
        yield c
        main.app.dependency_overrides.clear()


@pytest.fixture
def real_auth(client):
    """Turns the test-owner override off so a test exercises real authentication."""
    import backend.main as main
    from backend.auth import get_current_user

    saved = main.app.dependency_overrides.pop(get_current_user, None)
    yield
    if saved:
        main.app.dependency_overrides[get_current_user] = saved


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_properties_flag_uses_green_home_score(client):
    rows = client.get("/properties").json()
    assert len(rows) == 20
    for r in rows:
        assert r["flagged_for_intervention"] == (r["green_home_score"] < 50)


def test_ranking_is_sorted_and_filterable(client):
    rows = client.get("/properties/ranking").json()
    pots = [r["improvement_potential_pct"] for r in rows if r["improvement_potential_pct"] is not None]
    assert pots == sorted(pots, reverse=True)
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    flagged = client.get("/properties/ranking", params={"flagged_only": True}).json()
    assert flagged and all(r["flagged_for_intervention"] for r in flagged)


def test_full_retrofit_reports_components_and_installed(client):
    res = client.post(
        "/evaluate/full-retrofit",
        params={"persist": False},
        json={"property_id": "PROP-GB-020", "selected_interventions": WHOLE_HOME_PACKAGE},
    ).json()
    assert len(res["baseline_components"]) == 7
    assert "heat_pump" in res["already_installed"]


def test_chat_is_audited(client):
    res = client.post(
        "/scenario/chat",
        json={"user_query": "What if I add a heat pump with a 10k budget?", "property_id": "PROP-GB-002"},
    ).json()
    assert res["explanation_source"] == "template"
    assert res["within_budget"] is False
    logs = client.get("/audit/logs").json()
    assert logs[0]["query_text"].startswith("What if I add a heat pump")


def test_catalogue_and_portal(client):
    assert len(client.get("/catalogue").json()) == 10
    assert client.get("/health").json()["database"] == "connected"
    assert client.get("/portal/").status_code == 200
    assert client.get("/portal/app.html").status_code == 200


class _FakeAnthropic:
    """Stands in for anthropic.Anthropic; returns a canned reply."""

    reply = ""

    def __init__(self, *a, **k):
        pass

    def with_options(self, **k):
        return self

    @property
    def beta(self):
        return self

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=_FakeAnthropic.reply)],
        )


@pytest.mark.parametrize(
    "reply, expected_source, grounded",
    [
        ("- Save **£737** a year.", "llm", True),
        ("- Save **£950** a year.", "template", False),  # invented number -> rejected
    ],
)
def test_llm_reply_is_checked_against_audited_numbers(monkeypatch, reply, expected_source, grounded):
    import anthropic
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)
    _FakeAnthropic.reply = reply
    text, source, ok = llm_explainer.explain({"net_bill_savings_gbp": 737.24}, "bills?", "TEMPLATE")
    assert source == expected_source and ok is grounded
    assert text == (reply if source == "llm" else "TEMPLATE")


class _FakeHttpResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


@pytest.mark.parametrize(
    "provider, env, body",
    [
        ("gemini", {"GEMINI_API_KEY": "test-key"},
         {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "- Save **£737** a year."}]}}]}),
        ("ollama", {"OLLAMA_HOST": "localhost:11434"},
         {"message": {"role": "assistant", "content": "- Save **£737** a year."}}),
    ],
)
def test_gemini_and_ollama_replies_are_grounded(monkeypatch, provider, env, body):
    import httpx
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OLLAMA_HOST", "ECO_LLM_PROVIDER", "ECO_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: calls.append((url, kw)) or _FakeHttpResponse(body))

    assert llm_explainer.provider() == provider
    text, source, ok = llm_explainer.explain({"net_bill_savings_gbp": 737.24}, "bills?", "TEMPLATE")
    assert (text, source, ok) == ("- Save **£737** a year.", "llm", True)
    url, kw = calls[0]
    if provider == "gemini":
        assert "gemini-2.5-flash:generateContent" in url and "key=" not in url
        assert kw["headers"]["x-goog-api-key"] == "test-key"
    else:
        assert url == "http://localhost:11434/api/chat" and kw["json"]["model"] == "gemma4"


def test_gemini_safety_block_falls_back_to_template(monkeypatch):
    import httpx
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ECO_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _FakeHttpResponse({"promptFeedback": {"blockReason": "SAFETY"}}))
    assert llm_explainer.explain({"a": 1}, "q", "TEMPLATE") == ("TEMPLATE", "template", True)


# ---------- Auth & saved scenarios ----------

def test_local_auth_is_default_without_supabase(client, monkeypatch, real_auth):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    cfg = client.get("/auth/config").json()
    assert cfg["enabled"] is True and cfg["provider"] == "local"
    assert client.get("/scenarios").status_code == 401


def test_auth_can_be_turned_off(client, monkeypatch, real_auth):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.setenv("ECO_LOCAL_AUTH", "0")
    assert client.get("/auth/config").json()["enabled"] is False
    assert client.get("/scenarios").status_code == 503
    assert client.post("/auth/login", json={"email": "a@b.co", "password": "x"}).status_code == 404


def test_local_register_login_and_saved_scenarios(client, monkeypatch, real_auth):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    creds = {"email": "Carol@Example.com", "password": "correct-horse-1", "name": "Carol"}
    reg = client.post("/auth/register", json=creds)
    assert reg.status_code == 201
    assert reg.json()["user"]["email"] == "carol@example.com"
    assert client.post("/auth/register", json=creds).status_code == 409
    assert client.post("/auth/register", json={**creds, "email": "short@pw.io", "password": "123"}).status_code == 422

    assert client.post("/auth/login", json={"email": "carol@example.com", "password": "wrong-pass"}).status_code == 401
    login = client.post("/auth/login", json={"email": "CAROL@example.com", "password": "correct-horse-1"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = client.get("/auth/me", headers=headers).json()
    assert me["email"] == "carol@example.com" and me["name"] == "Carol"

    assert client.get("/properties", headers=headers).json() == []  # a new account starts empty
    home = client.post("/properties", headers=headers, json=NEW_HOME)
    assert home.status_code == 201
    pid = home.json()["property_id"]
    saved = client.post(
        "/scenarios", headers=headers,
        json={"property_id": pid, "name": "Loft only", "selected_interventions": ["loft_insulation"]},
    )
    assert saved.status_code == 201
    assert [s["name"] for s in client.get("/scenarios", headers=headers).json()] == ["Loft only"]
    assert client.get("/scenarios", headers={"Authorization": "Bearer forged.token.value"}).status_code == 401


def test_login_is_throttled_after_repeated_failures(client, monkeypatch, real_auth):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    body = {"email": "nobody@example.com", "password": "not-it-at-all"}
    codes = [client.post("/auth/login", json=body).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_scenarios_require_a_bearer_token(client, monkeypatch, real_auth):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    cfg = client.get("/auth/config").json()
    assert cfg["enabled"] is True and cfg["supabase_url"] == "https://example.supabase.co"
    assert client.get("/scenarios").status_code == 401


def test_saved_scenarios_are_private_to_their_owner(client):
    alice, bob = TEST_OWNER, OTHER_USER
    try:
        as_user(alice)
        saved = client.post(
            "/scenarios",
            json={"property_id": "PROP-GB-001", "name": "Heat pump plan", "selected_interventions": ["heat_pump"]},
        )
        assert saved.status_code == 201
        sid = saved.json()["scenario_id"]
        assert [s["scenario_id"] for s in client.get("/scenarios").json()] == [sid]
        detail = client.get(f"/scenarios/{sid}").json()
        assert detail["assumptions"]["emission_factors_kg_co2e_per_kwh"]["gas"] == 0.18316
        assert "draught_proofing" in detail["result"]["dag_plan"]["injected_prerequisites"]

        as_user(bob)
        assert client.get("/scenarios").json() == []
        assert client.get(f"/scenarios/{sid}").status_code == 404
        assert client.delete(f"/scenarios/{sid}").status_code == 404

        as_user(alice)
        assert client.delete(f"/scenarios/{sid}").status_code == 204
        assert client.get("/scenarios").json() == []
    finally:
        as_user(TEST_OWNER)


def test_saved_scenario_rejects_unknown_measures_and_blank_names(client):
    body = {"property_id": "PROP-GB-001", "name": "Test", "selected_interventions": ["loft_insulation"]}
    assert client.post("/scenarios", json={**body, "selected_interventions": ["unknown_measure"]}).status_code == 422
    assert client.post("/scenarios", json={**body, "name": "   "}).status_code == 422
    saved = client.post("/scenarios", json={**body, "name": "  Valid name  "})
    assert saved.status_code == 201
    assert saved.json()["name"] == "Valid name"
    assert client.delete(f"/scenarios/{saved.json()['scenario_id']}").status_code == 204


# ---------- Computer vision endpoint ----------

def test_vision_endpoint_segments_thermal_and_rejects_bad_files(client):
    import cv2
    import numpy as np

    t = np.full((300, 400), 40, np.uint8)
    cv2.rectangle(t, (150, 120), (230, 190), 230, -1)
    ok, buf = cv2.imencode(".jpg", cv2.applyColorMap(cv2.GaussianBlur(t, (0, 0), 3), cv2.COLORMAP_INFERNO))
    r = client.post("/vision/analyze", files={"file": ("t.jpg", buf.tobytes(), "image/jpeg")}, data={"mode": "thermal"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "thermal" and body["image_stored"] is False and body["detections"]
    bad = client.post("/vision/analyze", files={"file": ("x.jpg", b"nope", "image/jpeg")}, data={"mode": "auto"})
    assert bad.status_code == 422
    assert client.get("/vision/status").json()["images_stored"] is False


# ---------- Efficiency: portfolio cache, compression, static caching ----------

def test_portfolio_is_cached_until_property_data_changes(client):
    from backend.db import database
    from backend.services.assessment_service import portfolio_cache

    first = client.get("/properties/ranking").json()
    misses = portfolio_cache.misses
    assert client.get("/properties/ranking").json() == first
    assert client.get("/properties").status_code == 200
    assert portfolio_cache.misses == misses  # served from cache

    db = database.SessionLocal()
    try:
        prop = db.query(database.PropertyModel).filter_by(property_id=first[-1]["property_id"]).one()
        original = prop.smart_controls
        prop.smart_controls = not original
        db.commit()
        changed = client.get("/properties/ranking").json()
        assert portfolio_cache.misses == misses + 1  # data edit invalidates the cache
        prop.smart_controls = original
        db.commit()
    finally:
        db.close()
    assert [r["property_id"] for r in changed]  # still a full ranking


def test_ranking_filter_renumbers_from_one(client):
    flagged = client.get("/properties/ranking", params={"flagged_only": True}).json()
    assert flagged and all(r["flagged_for_intervention"] for r in flagged)
    assert [r["rank"] for r in flagged] == list(range(1, len(flagged) + 1))


def test_preview_evaluations_are_not_persisted(client):
    from backend.db import database

    def count():
        db = database.SessionLocal()
        try:
            return db.query(database.RetrofitEvaluationModel).count()
        finally:
            db.close()

    body = {"property_id": "PROP-GB-001", "selected_interventions": ["heat_pump"]}
    before = count()
    assert client.post("/evaluate/full-retrofit?persist=false", json=body).status_code == 200
    assert count() == before
    assert client.post("/evaluate/full-retrofit", json=body).status_code == 200
    assert count() == before + 1


def test_responses_are_compressed_and_static_files_cached(client):
    r = client.get("/properties/ranking", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    css = client.get("/portal/style.css", headers={"Accept-Encoding": "gzip"})
    assert css.headers.get("content-encoding") == "gzip"
    assert "max-age" in css.headers["cache-control"]
    img = client.get("/portal/assets/eco_house_3d.webp")
    assert "max-age=604800" in img.headers["cache-control"]
    assert client.get("/portal/app.html").headers["cache-control"] == "no-cache"


def test_epc_prediction_endpoint(client):
    r = client.get("/properties/PROP-GB-001/epc-prediction")
    assert r.status_code == 200
    body = r.json()
    assert body["property_id"] == "PROP-GB-001"
    if body["available"]:
        assert body["potential_band"] in "ABCDEFG" and body["inputs"]["TOTAL_FLOOR_AREA"] > 0
    assert client.get("/properties/NOPE/epc-prediction").status_code == 404


def test_segment_endpoint_validates_boxes(client):
    files = {"file": ("x.jpg", b"nope", "image/jpeg")}
    assert client.post("/vision/segment", files=files, data={"boxes": "not json"}).status_code == 422
    assert client.post("/vision/segment", files=files, data={"boxes": "[[0.5,0.5,0.1,0.1]]"}).status_code == 422



# ---------- Ownership, CRUD and account data ----------

NEW_HOME = {
    "address": "1 Test Street, Leeds", "postcode": "ls1 4ab", "region": "Yorkshire and The Humber",
    "property_type": "1930s Semi-Detached", "built_year": 1934, "total_floor_area_m2": 88,
    "baseline_value_gbp": 260000, "current_epc_score": 52, "wall_type": "Cavity Wall Unfilled",
    "wall_insulation_status": "Uninsulated", "wall_u_value": 1.5, "roof_insulation_depth_mm": 100,
    "floor_type": "Suspended Timber", "floor_insulation_status": "Uninsulated",
    "glazing_type": "Older Double Glazed (pre-2002)", "heating_type": "Gas Boiler (Condensing)",
    "heating_efficiency_pct": 88, "annual_gas_kwh": 13500, "annual_electricity_kwh": 3100,
}


def test_create_property_derives_fields_and_is_owned(client):
    r = client.post("/properties", json=NEW_HOME)
    assert r.status_code == 201
    body = r.json()
    assert body["property_id"].startswith("PROP-") and len(body["property_id"]) == 13
    assert body["current_epc_rating"] == "E"           # SAP 52 -> band E
    assert body["roof_insulation_status"] == "Partial"  # 100 mm loft
    assert body["postcode"] == "LS1 4AB"
    assert body["current_primary_energy_kwh_m2"] == round((13500 * 1.13 + 3100 * 1.501) / 88, 1)
    pid = body["property_id"]
    assert any(p["property_id"] == pid for p in client.get("/properties").json())
    assert client.delete(f"/properties/{pid}").status_code == 204


def test_create_property_validates_input(client):
    assert client.post("/properties", json={**NEW_HOME, "total_floor_area_m2": -5}).status_code == 422
    assert client.post("/properties", json={**NEW_HOME, "current_epc_score": 150}).status_code == 422
    assert client.post("/properties", json={**NEW_HOME, "postcode": "<script>"}).status_code == 422


def test_update_recomputes_derived_fields(client):
    pid = client.post("/properties", json=NEW_HOME).json()["property_id"]
    r = client.patch(f"/properties/{pid}", json={"current_epc_score": 85, "roof_insulation_depth_mm": 300})
    assert r.status_code == 200
    assert r.json()["current_epc_rating"] == "B" and r.json()["roof_insulation_status"] == "Good"
    client.delete(f"/properties/{pid}")


def test_update_rejects_null_required_fields(client):
    pid = client.post("/properties", json=NEW_HOME).json()["property_id"]
    try:
        for field in ("address", "current_epc_score", "annual_gas_kwh", "smart_controls"):
            response = client.patch(f"/properties/{pid}", json={field: None})
            assert response.status_code == 422, field
        assert client.get(f"/properties/{pid}").json()["address"] == NEW_HOME["address"]
    finally:
        client.delete(f"/properties/{pid}")


def test_homes_are_invisible_to_other_users(client):
    pid = client.post("/properties", json=NEW_HOME).json()["property_id"]
    try:
        as_user(OTHER_USER)
        assert client.get("/properties").json() == []
        assert client.get("/properties/ranking").json() == []
        assert client.get(f"/properties/{pid}").status_code == 404
        assert client.patch(f"/properties/{pid}", json={"address": "Stolen"}).status_code == 404
        assert client.delete(f"/properties/{pid}").status_code == 404
        assert client.get(f"/properties/{pid}/epc-prediction").status_code == 404
        body = {"property_id": pid, "selected_interventions": ["heat_pump"]}
        assert client.post("/evaluate/full-retrofit?persist=false", json=body).status_code == 404
        assert client.post("/scenarios", json={**body, "name": "x"}).status_code == 404
        assert client.post("/scenario/chat", json={"user_query": "heat pump?", "property_id": pid}).status_code == 404
        # Naming someone else's home inside the question is rejected too.
        own = client.post("/properties", json=NEW_HOME).json()["property_id"]
        leak = client.post("/scenario/chat", json={"user_query": f"what about {pid} with a heat pump", "property_id": own})
        assert leak.status_code == 404
        assert client.get("/audit/logs").json() == []
    finally:
        as_user(TEST_OWNER)
    assert client.get(f"/properties/{pid}").status_code == 200
    client.delete(f"/properties/{pid}")


def test_deleting_a_home_removes_its_scenarios(client):
    pid = client.post("/properties", json=NEW_HOME).json()["property_id"]
    sid = client.post("/scenarios", json={"property_id": pid, "name": "tmp", "selected_interventions": ["heat_pump"]}).json()["scenario_id"]
    assert client.delete(f"/properties/{pid}").status_code == 204
    assert client.get(f"/scenarios/{sid}").status_code == 404


def test_delete_account_removes_all_user_data(client, real_auth):
    creds = {"email": "dave@example.com", "password": "another-pass-1", "name": "Dave"}
    token = client.post("/auth/register", json=creds).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    pid = client.post("/properties", headers=h, json=NEW_HOME).json()["property_id"]
    client.post("/scenarios", headers=h, json={"property_id": pid, "name": "x", "selected_interventions": ["heat_pump"]})
    assert client.delete("/auth/me", headers=h).status_code == 204
    assert client.get("/auth/me", headers=h).status_code == 401
    assert client.post("/auth/login", json={"email": creds["email"], "password": creds["password"]}).status_code == 401
    from backend.db import database
    db = database.SessionLocal()
    try:
        assert db.query(database.PropertyModel).filter_by(property_id=pid).count() == 0
        assert db.query(database.SavedScenarioModel).filter_by(property_id=pid).count() == 0
    finally:
        db.close()


def test_data_endpoints_require_sign_in(client, real_auth):
    for path in ["/properties", "/properties/ranking", "/properties/PROP-GB-001", "/audit/logs", "/scenarios"]:
        assert client.get(path).status_code == 401, path
    assert client.post("/evaluate/full-retrofit", json={"property_id": "PROP-GB-001", "selected_interventions": []}).status_code == 401
    assert client.post("/properties", json=NEW_HOME).status_code == 401

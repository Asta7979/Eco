"""
Security regression tests: headers/CSP, rate and size limits, password rules, session
revocation, deactivation, admin RBAC, upload hardening and production configuration.
"""

import importlib
import re
import struct
import zlib
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "frontend" / "web"


@pytest.fixture(scope="module")
def mp():
    m = pytest.MonkeyPatch()
    yield m
    m.undo()


@pytest.fixture(scope="module")
def client(tmp_path_factory, mp):
    mp.setenv("DATABASE_URL", f"sqlite:///{tmp_path_factory.mktemp('secdb') / 'sec.db'}")
    mp.setenv("ECO_VISION_WARMUP", "0")
    mp.setenv("ECO_AUTH_SECRET", "security-test-secret-at-least-32-chars")
    mp.setenv("ECO_RATE_LIMIT", "0")
    mp.setenv("ECO_SUPERUSER_EMAILS", "root@example.com")
    mp.delenv("SUPABASE_URL", raising=False)
    mp.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    mp.delenv("SUPABASE_ANON_KEY", raising=False)
    import backend.db.database as database
    import backend.main as main

    importlib.reload(database)
    importlib.reload(main)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        main.app.dependency_overrides.clear()
        yield c


def register(client, email, password="strong-pass-1", name="Test"):
    r = client.post("/auth/register", json={"email": email, "password": password, "name": name})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------- Headers and CSP ----------

def test_security_headers_on_pages_and_api(client):
    for path in ["/portal/", "/portal/app.html", "/auth/config", "/health"]:
        r = client.get(path)
        h = r.headers
        assert h["x-frame-options"] == "DENY", path
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "camera=(self)" in h["permissions-policy"]
        assert "server" not in {k.lower() for k in h.keys()}
        csp = h["content-security-policy"]
        script_src = re.search(r"script-src ([^;]+)", csp).group(1)
        assert "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src
        assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp


def test_personal_data_responses_are_not_cached(client):
    h = register(client, "cache@example.com")
    assert client.get("/properties", headers=h).headers["cache-control"] == "no-store"


def test_html_pages_have_no_inline_scripts_or_handlers():
    for page in WEB.glob("*.html"):
        html = page.read_text(encoding="utf-8")
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), f"inline <script> in {page.name}"
        assert not re.search(r"\son[a-z]+\s*=\s*[\"']", html), f"inline event handler in {page.name}"
        assert "javascript:" not in html.lower(), page.name


# ---------- Rate and size limits ----------

def test_login_rate_limit_per_ip(client, monkeypatch):
    from backend.core.security import reset_rate_limits

    monkeypatch.setenv("ECO_RATE_LIMIT", "1")
    reset_rate_limits()
    body = {"email": "nobody-rl@example.com", "password": "whatever-123"}
    codes = [client.post("/auth/login", json=body).status_code for _ in range(12)]
    assert 429 in codes
    blocked = client.post("/auth/login", json=body)
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1
    reset_rate_limits()


def test_oversized_json_body_is_rejected(client):
    big = {"email": "a@b.co", "password": "x" * 300_000}
    assert client.post("/auth/login", json=big).status_code == 413


# ---------- Passwords and sessions ----------

def test_password_rules(client):
    weak = client.post("/auth/register", json={"email": "w@example.com", "password": "short"})
    assert weak.status_code == 422
    too_long = client.post("/auth/register", json={"email": "w@example.com", "password": "é" * 40})
    assert too_long.status_code == 422  # 80 bytes: would crash bcrypt (500) before the fix
    assert "72 bytes" in too_long.text


def test_unknown_email_and_wrong_password_look_identical(client):
    register(client, "known@example.com")
    a = client.post("/auth/login", json={"email": "known@example.com", "password": "wrong-pass-1"})
    b = client.post("/auth/login", json={"email": "unknown@example.com", "password": "wrong-pass-1"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_password_change_revokes_old_sessions(client):
    old = register(client, "pw@example.com", "first-pass-1")
    bad = client.post("/auth/password", headers=old, json={"current_password": "nope-nope-1", "new_password": "second-pass-2"})
    assert bad.status_code == 400
    ok = client.post("/auth/password", headers=old, json={"current_password": "first-pass-1", "new_password": "second-pass-2"})
    assert ok.status_code == 204
    assert client.get("/auth/me", headers=old).status_code == 401  # old token revoked
    assert client.post("/auth/login", json={"email": "pw@example.com", "password": "first-pass-1"}).status_code == 401
    new = client.post("/auth/login", json={"email": "pw@example.com", "password": "second-pass-2"})
    assert new.status_code == 200
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {new.json()['access_token']}"}).status_code == 200

    # Even when an older token has the same one-second iat as the new token,
    # its password version must fail verification.
    import jwt
    from backend.auth import _jwt_secret

    old_claims = jwt.decode(old["Authorization"].split()[1], options={"verify_signature": False})
    new_claims = jwt.decode(new.json()["access_token"], options={"verify_signature": False})
    old_claims["iat"] = new_claims["iat"]
    old_claims["exp"] = new_claims["exp"]
    same_second_old_token = jwt.encode(old_claims, _jwt_secret(), algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {same_second_old_token}"}).status_code == 401


def test_forged_and_tampered_tokens_are_rejected(client):
    import jwt

    h = register(client, "tamper@example.com")
    token = h["Authorization"].split()[1]
    payload = jwt.decode(token, options={"verify_signature": False})
    payload["sub"] = "someone-else"
    forged = jwt.encode(payload, "not-the-server-secret-but-long-enough", algorithm="HS256")
    none_alg = jwt.encode(payload, None, algorithm="none")
    for bad in [forged, none_alg, token[:-4] + "AAAA"]:
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {bad}"}).status_code == 401


# ---------- RBAC ----------

def test_admin_routes_require_superuser(client):
    user = register(client, "plain@example.com")
    assert client.get("/admin/users", headers=user).status_code == 403
    assert client.get("/admin/stats", headers=user).status_code == 403
    assert client.get("/admin/users").status_code == 401


def test_admin_can_deactivate_and_activate_but_not_demote_self(client):
    root = register(client, "root@example.com")
    assert client.get("/auth/me", headers=root).json()["is_superuser"] is True
    victim = register(client, "victim@example.com")
    victim_id = client.get("/auth/me", headers=victim).json()["id"]
    root_id = client.get("/auth/me", headers=root).json()["id"]

    users = client.get("/admin/users", headers=root).json()
    assert any(u["email"] == "victim@example.com" for u in users)

    assert client.patch(f"/admin/users/{victim_id}", headers=root, json={"is_active": False}).status_code == 200
    assert client.get("/auth/me", headers=victim).status_code == 403  # existing session stops working
    assert client.post("/auth/login", json={"email": "victim@example.com", "password": "strong-pass-1"}).status_code == 403
    assert client.patch(f"/admin/users/{victim_id}", headers=root, json={"is_active": True}).status_code == 200
    assert client.get("/auth/me", headers=victim).status_code == 200

    assert client.patch(f"/admin/users/{root_id}", headers=root, json={"is_superuser": False}).status_code == 400
    assert client.delete(f"/admin/users/{root_id}", headers=root).status_code == 400
    assert client.delete(f"/admin/users/{victim_id}", headers=root).status_code == 204
    assert client.get("/auth/me", headers=victim).status_code == 401


def test_calculation_endpoints_require_sign_in(client):
    body = {"baseline_gas_kwh": 1000, "baseline_electricity_kwh": 1000, "interventions": ["heat_pump"], "property_floor_area_m2": 80}
    assert client.post("/evaluate/carbon", json=body).status_code == 401
    h = register(client, "calc@example.com")
    assert client.post("/evaluate/carbon", headers=h, json=body).status_code == 200
    assert client.post("/evaluate/carbon", headers=h, json={**body, "interventions": ["x"] * 25}).status_code == 422


# ---------- Uploads ----------

def _png_header(width, height):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" * 16)) + chunk(b"IEND", b"")


def test_decompression_bomb_and_odd_formats_are_rejected():
    from backend.engine.vision_engine import decode_image

    with pytest.raises(ValueError, match="too large"):
        decode_image(_png_header(30_000, 30_000))  # 900 MP declared in a tiny file
    tiff = b"II*\x00" + b"\x00" * 64
    with pytest.raises(ValueError):
        decode_image(tiff)


def test_vision_status_does_not_leak_internal_errors(client):
    body = client.get("/vision/status").json()

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "error":
                    assert isinstance(x, bool)
                walk(x)
    walk(body)


# ---------- Production configuration ----------

def test_production_disables_docs_and_rejects_wildcard_cors(monkeypatch):
    from backend.core.config import Settings

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ECO_ENABLE_DOCS", raising=False)
    assert Settings().docs_enabled is False
    monkeypatch.setenv("ECO_CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError):
        Settings().cors_origins
    monkeypatch.setenv("ECO_CORS_ORIGINS", "https://eco.example.com")
    assert Settings().cors_origins == ["https://eco.example.com"]


def test_default_cors_is_same_origin_only(client):
    r = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers.keys()}


def test_unhandled_errors_do_not_leak_details(client, monkeypatch):
    from backend.api.routes import system

    def boom(*a, **k):
        raise RuntimeError("secret internal detail C:/path/to/db")
    monkeypatch.setattr(system.portfolio_cache, "stats", boom)
    from fastapi.testclient import TestClient
    import backend.main as main

    with TestClient(main.app, raise_server_exceptions=False) as c:
        r = c.get("/health")
    assert r.status_code == 500 and "secret" not in r.text

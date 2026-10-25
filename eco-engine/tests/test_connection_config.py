"""Checks the database and signing configuration used by local and Docker runs."""

import pytest
from sqlalchemy.engine import URL

from backend.auth import supabase_settings, validate_auth_configuration
from backend.db.database import connection_args, database_url


def test_postgres_connection_preserves_password_with_url_characters(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_USER", "eco_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "a:b@c/d#e")
    monkeypatch.setenv("POSTGRES_DB", "eco_db")

    url = database_url()
    assert isinstance(url, URL)
    assert url.password == "a:b@c/d#e"
    assert url.host == "db"
    assert url.database == "eco_db"


def test_database_url_takes_precedence_over_compose_fields(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///explicit.db")
    monkeypatch.setenv("POSTGRES_HOST", "db")
    assert database_url() == "sqlite:///explicit.db"


def test_supabase_database_uses_tls():
    url = "postgresql+psycopg2://postgres:pw@db.tajtlvwdgdkrxisuqyrm.supabase.co:5432/postgres"
    assert connection_args(url) == {"sslmode": "require"}


def test_production_local_auth_requires_stable_secret(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.delenv("ECO_AUTH_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="ECO_AUTH_SECRET"):
        validate_auth_configuration()

    monkeypatch.setenv("ECO_AUTH_SECRET", "a" * 32)
    validate_auth_configuration()


def test_partial_supabase_configuration_fails(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_URL and a publishable"):
        validate_auth_configuration()


def test_rest_url_is_normalized_for_supabase_auth(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://tajtlvwdgdkrxisuqyrm.supabase.co/rest/v1/")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test")
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    assert supabase_settings() == (
        "https://tajtlvwdgdkrxisuqyrm.supabase.co", "sb_publishable_test"
    )
    validate_auth_configuration()

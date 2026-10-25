"""Vercel entrypoint: serves the gateway (API + web portal) as one Python function."""

from backend.main import app  # noqa: F401

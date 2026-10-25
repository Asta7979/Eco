"""
Schema management at start-up (Alembic, as in fastapi/full-stack-fastapi-template).

* Fresh database            -> `alembic upgrade head` creates every table.
* Alembic-managed database  -> `alembic upgrade head` applies any new migrations.
* Pre-Alembic database      -> the legacy in-place column migration brings it up to the
                               current schema, then it is stamped at head (no data touched).
Afterwards init_db() runs (create_all is a no-op; on Postgres it enables row-level security).
"""

import logging

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from backend.core.config import BASE_DIR
from backend.db import database

logger = logging.getLogger("eco.migrate")
APP_TABLES = {"properties", "users", "saved_scenarios", "retrofit_evaluations", "audit_logs"}


def alembic_config() -> Config:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    return cfg


def ensure_schema() -> str:
    tables = set(inspect(database.engine).get_table_names())
    cfg = alembic_config()
    if "alembic_version" in tables or not (tables & APP_TABLES):
        command.upgrade(cfg, "head")
        action = "upgraded"
    else:
        database.init_db()          # legacy path: add any missing columns in place
        command.stamp(cfg, "head")  # adopt the existing schema under Alembic
        action = "stamped"
    database.init_db()
    logger.info("Database schema %s to the latest migration", action)
    return action

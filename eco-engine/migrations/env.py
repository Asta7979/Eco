"""Alembic environment: uses the app's own models and connection settings."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import backend  # noqa: F401  (loads .env)
from backend.db.database import Base, database_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)  # keep the app's loggers

target_metadata = Base.metadata


def _url() -> str:
    url = database_url()
    return url.render_as_string(hide_password=False) if hasattr(url, "render_as_string") else str(url)


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        # render_as_batch lets ALTER TABLE migrations work on SQLite too.
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

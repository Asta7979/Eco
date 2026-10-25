"""
Database handler for Project Eco.
Supports SQLite (local development) and PostgreSQL 16 (production) with SQLAlchemy 2.0.
"""

import os
import tempfile
from pathlib import Path
from typing import Generator
from datetime import datetime

from sqlalchemy import (
    create_engine,
    Column,
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Text,
    Numeric,
    ForeignKey,
)
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from backend.core.config import BASE_DIR

# Determine database URL
DEFAULT_SQLITE_PATH = BASE_DIR / "eco_engine.db"

def database_url():
    """Resolve a full URL, PostgreSQL environment fields, or the local SQLite file."""
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if os.getenv("POSTGRES_HOST"):
        password = os.getenv("POSTGRES_PASSWORD")
        if not password:
            raise RuntimeError("POSTGRES_PASSWORD is required when POSTGRES_HOST is set")
        return URL.create(
            "postgresql+psycopg2",
            username=os.getenv("POSTGRES_USER", "eco_user"),
            password=password,
            host=os.environ["POSTGRES_HOST"],
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "eco_db"),
        )
    if os.getenv("VERCEL"):
        # The deployment filesystem is read-only; /tmp works but is wiped between instances.
        # Set DATABASE_URL (e.g. Supabase Postgres) for anything that must persist.
        return f"sqlite:///{Path(tempfile.gettempdir()) / 'eco_engine.db'}"
    return f"sqlite:///{DEFAULT_SQLITE_PATH}"


DATABASE_URL = database_url()
IS_SQLITE = str(DATABASE_URL).startswith("sqlite")

# Configure SQLAlchemy engine
def connection_args(url):
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        return {"check_same_thread": False}
    host = parsed.host or ""
    if parsed.get_backend_name() == "postgresql" and host.endswith((".supabase.co", ".pooler.supabase.com")):
        return {"sslmode": "require"}
    return {}


connect_args = connection_args(DATABASE_URL)

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False,
    future=True,
    pool_pre_ping=not IS_SQLITE,  # drop dead Postgres connections
)

if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")     # enforce ownership / cascade rules
        cur.execute("PRAGMA journal_mode=WAL")    # readers don't block the writer
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


class PropertyModel(Base):
    __tablename__ = "properties"

    property_id = Column(String(64), primary_key=True, index=True)
    # The signed-in user who added this home; every query is scoped to it.
    owner_uid = Column(String(64), nullable=True, index=True)
    address = Column(String(255), nullable=False)
    postcode = Column(String(16), nullable=False, index=True)
    region = Column(String(64), nullable=False, index=True)
    property_type = Column(String(64), nullable=False)
    built_year = Column(Integer, nullable=False)
    total_floor_area_m2 = Column(Float, nullable=False)
    baseline_value_gbp = Column(Float, nullable=False)

    # Current EPC & Energy Metrics
    current_epc_rating = Column(String(2), nullable=False)
    current_epc_score = Column(Integer, nullable=False)
    current_primary_energy_kwh_m2 = Column(Float, nullable=False)

    # Fabric & Thermal Characteristics
    wall_type = Column(String(64), nullable=False)
    wall_insulation_status = Column(String(64), nullable=False)
    wall_u_value = Column(Float, nullable=False)
    roof_type = Column(String(64), nullable=False)
    roof_insulation_status = Column(String(64), nullable=False)
    roof_insulation_depth_mm = Column(Integer, nullable=False)
    floor_type = Column(String(64), nullable=False)
    floor_insulation_status = Column(String(64), nullable=False)
    glazing_type = Column(String(64), nullable=False)

    # Heating & Renewables
    heating_type = Column(String(64), nullable=False)
    heating_efficiency_pct = Column(Float, nullable=False)
    annual_gas_kwh = Column(Float, nullable=False)
    annual_electricity_kwh = Column(Float, nullable=False)
    solar_pv_kwp = Column(Float, default=0.0)
    battery_storage_kwh = Column(Float, default=0.0)
    smart_controls = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RetrofitEvaluationModel(Base):
    __tablename__ = "retrofit_evaluations"

    evaluation_id = Column(String(64), primary_key=True, index=True)
    property_id = Column(String(64), ForeignKey("properties.property_id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(64), nullable=True, index=True)
    baseline_score = Column(Integer, nullable=False)
    projected_score = Column(Integer, nullable=False)
    score_delta = Column(Integer, nullable=False)
    flagged_for_intervention = Column(Boolean, nullable=False)

    # Decoupled Carbon & Energy
    delta_gas_kwh = Column(Float, nullable=False)
    delta_electricity_kwh = Column(Float, nullable=False)
    annual_co2e_saved_kg = Column(Float, nullable=False)
    annual_cost_savings_gbp = Column(Float, nullable=False)
    heat_pump_paradox_flag = Column(Boolean, default=False)

    # Hedonic Valuation
    predicted_uplift_gbp_min = Column(Float, nullable=False)
    predicted_uplift_gbp_max = Column(Float, nullable=False)
    predicted_uplift_pct_min = Column(Float, nullable=False)
    predicted_uplift_pct_max = Column(Float, nullable=False)

    # Recommended DAG Plan
    topological_roadmap_json = Column(Text, nullable=False)
    interventions_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLogModel(Base):
    __tablename__ = "audit_logs"

    log_id = Column(String(64), primary_key=True, index=True)
    property_id = Column(String(64), nullable=True, index=True)
    user_id = Column(String(64), nullable=True, index=True)
    query_text = Column(Text, nullable=False)
    extracted_parameters_json = Column(Text, nullable=False)
    deterministic_result_json = Column(Text, nullable=False)
    guardrail_status = Column(String(32), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


class UserModel(Base):
    """Local email + password account (used when Supabase Auth is not configured)."""
    __tablename__ = "users"

    user_id = Column(String(64), primary_key=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    name = Column(String(120), nullable=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)
    # Roles (pattern from full-stack-fastapi-template): inactive accounts cannot sign in or
    # use existing sessions; superusers can manage other accounts via /admin.
    is_active = Column(Boolean, nullable=False, default=True)
    is_superuser = Column(Boolean, nullable=False, default=False)
    # Sessions issued before this moment are rejected (password change / admin reset).
    password_changed_at = Column(DateTime, nullable=True)


class SavedScenarioModel(Base):
    """
    A user's saved what-if scenario. Immutable once saved: it keeps the full
    audited result plus the assumptions and engine version that produced it.
    """
    __tablename__ = "saved_scenarios"

    scenario_id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    user_email = Column(String(255), nullable=True)
    property_id = Column(String(64), ForeignKey("properties.property_id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    measures_json = Column(Text, nullable=False)
    result_json = Column(Text, nullable=False)
    assumptions_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# Columns added after the first release: (table, column, SQL type). create_all() does not
# alter existing tables, so these are added in place on start-up (idempotent).
_ADDED_COLUMNS = [
    ("properties", "owner_uid", "VARCHAR(64)"),
    ("retrofit_evaluations", "user_id", "VARCHAR(64)"),
    ("audit_logs", "user_id", "VARCHAR(64)"),
    ("users", "is_active", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ("users", "is_superuser", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("users", "password_changed_at", "TIMESTAMP"),
]


def _migrate() -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, column, sql_type in _ADDED_COLUMNS:
            if table in insp.get_table_names() and column not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {sql_type}'))
                conn.execute(text(f'CREATE INDEX IF NOT EXISTS "ix_{table}_{column}" ON "{table}" ("{column}")'))


def init_db() -> None:
    """Creates missing tables and applies in-place column migrations."""
    Base.metadata.create_all(bind=engine)
    _migrate()

    # On Postgres (including Supabase), switch on row-level security with no
    # policies. Supabase's public REST API (anon key) then cannot read these
    # tables, while this backend's direct table-owner connection is unaffected.
    if engine.dialect.name == "postgresql":
        from sqlalchemy import text
        with engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                conn.execute(text(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY'))


def get_db() -> Generator[Session, None, None]:
    """Dependency generator for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

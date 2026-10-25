-- EcoRetrofit AI Engine schema reference (PostgreSQL & SQLite compatible).
-- The application creates and migrates tables through backend/db/database.py.
-- Project Eco for Lloyds Banking Group

CREATE TABLE IF NOT EXISTS properties (
    property_id VARCHAR(64) PRIMARY KEY,
    owner_uid VARCHAR(64),
    address VARCHAR(255) NOT NULL,
    postcode VARCHAR(16) NOT NULL,
    region VARCHAR(64) NOT NULL,
    property_type VARCHAR(64) NOT NULL,
    built_year INTEGER NOT NULL,
    total_floor_area_m2 NUMERIC(10, 2) NOT NULL,
    baseline_value_gbp NUMERIC(12, 2) NOT NULL,
    
    -- Current EPC & Energy Metrics
    current_epc_rating VARCHAR(2) NOT NULL,
    current_epc_score INTEGER NOT NULL,
    current_primary_energy_kwh_m2 NUMERIC(10, 2) NOT NULL,
    
    -- Fabric & Thermal Characteristics
    wall_type VARCHAR(64) NOT NULL,
    wall_insulation_status VARCHAR(64) NOT NULL,
    wall_u_value NUMERIC(6, 3) NOT NULL,
    roof_type VARCHAR(64) NOT NULL,
    roof_insulation_status VARCHAR(64) NOT NULL,
    roof_insulation_depth_mm INTEGER NOT NULL,
    floor_type VARCHAR(64) NOT NULL,
    floor_insulation_status VARCHAR(64) NOT NULL,
    glazing_type VARCHAR(64) NOT NULL,
    
    -- Heating & Renewables
    heating_type VARCHAR(64) NOT NULL,
    heating_efficiency_pct NUMERIC(6, 2) NOT NULL,
    annual_gas_kwh NUMERIC(12, 2) NOT NULL,
    annual_electricity_kwh NUMERIC(12, 2) NOT NULL,
    solar_pv_kwp NUMERIC(6, 2) DEFAULT 0.0,
    battery_storage_kwh NUMERIC(6, 2) DEFAULT 0.0,
    smart_controls BOOLEAN DEFAULT FALSE,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS retrofit_evaluations (
    evaluation_id VARCHAR(64) PRIMARY KEY,
    property_id VARCHAR(64) REFERENCES properties(property_id) ON DELETE CASCADE,
    user_id VARCHAR(64),
    baseline_score INTEGER NOT NULL,
    projected_score INTEGER NOT NULL,
    score_delta INTEGER NOT NULL,
    flagged_for_intervention BOOLEAN NOT NULL,
    
    -- Decoupled Carbon & Energy
    delta_gas_kwh NUMERIC(12, 2) NOT NULL,
    delta_electricity_kwh NUMERIC(12, 2) NOT NULL,
    annual_co2e_saved_kg NUMERIC(12, 2) NOT NULL,
    annual_cost_savings_gbp NUMERIC(10, 2) NOT NULL,
    heat_pump_paradox_flag BOOLEAN DEFAULT FALSE,
    
    -- Hedonic Valuation
    predicted_uplift_gbp_min NUMERIC(12, 2) NOT NULL,
    predicted_uplift_gbp_max NUMERIC(12, 2) NOT NULL,
    predicted_uplift_pct_min NUMERIC(6, 2) NOT NULL,
    predicted_uplift_pct_max NUMERIC(6, 2) NOT NULL,
    
    -- Recommended DAG Plan (JSON string)
    topological_roadmap_json TEXT NOT NULL,
    interventions_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id VARCHAR(64) PRIMARY KEY,
    property_id VARCHAR(64),
    user_id VARCHAR(64),
    query_text TEXT NOT NULL,
    extracted_parameters_json TEXT NOT NULL,
    deterministic_result_json TEXT NOT NULL,
    guardrail_status VARCHAR(32) NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(64) PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(120),
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS saved_scenarios (
    scenario_id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    user_email VARCHAR(255),
    property_id VARCHAR(64) NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
    name VARCHAR(120) NOT NULL,
    measures_json TEXT NOT NULL,
    result_json TEXT NOT NULL,             -- immutable audited FullRetrofitResponse
    assumptions_json TEXT NOT NULL,        -- emission factors, tariffs, engine version
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_saved_scenarios_user ON saved_scenarios(user_id);
CREATE INDEX IF NOT EXISTS idx_properties_owner_uid ON properties(owner_uid);
CREATE INDEX IF NOT EXISTS idx_evaluations_user_id ON retrofit_evaluations(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_properties_postcode ON properties(postcode);
CREATE INDEX IF NOT EXISTS idx_properties_region ON properties(region);
CREATE INDEX IF NOT EXISTS idx_evaluations_property ON retrofit_evaluations(property_id);

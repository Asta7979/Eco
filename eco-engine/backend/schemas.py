"""
Pydantic v2 schemas for EcoRetrofit AI Engine (Project Eco).
Provides strict validation and serialisation across deterministic calculation engines
and guarded LLM scenario layers.
"""

from typing import Annotated, List, Dict, Optional, Any
from pydantic import BaseModel, Field, ConfigDict, StringConstraints, field_validator, model_validator

# Bounded building blocks: every list, code and free-text field has a size limit so no
# request can make the engines (or the database) do unbounded work.
ActionCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,39}$")]
ActionCodes = Annotated[List[ActionCode], Field(max_length=20)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=64)]
PropertyId = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


def _known_action_codes(codes: List[str]) -> List[str]:
    """Reject measure codes that are not in the retrofit catalogue, instead of silently
    dropping them (a typo such as 'air_source_heat_pump' would otherwise return an empty plan)."""
    from backend.engine.dag_engine import RETROFIT_ACTIONS_CATALOGUE  # lazy: dag_engine imports this module

    unknown = [c for c in codes if c not in RETROFIT_ACTIONS_CATALOGUE]
    if unknown:
        raise ValueError(f"Unknown measure code(s) {unknown}; valid codes: {sorted(RETROFIT_ACTIONS_CATALOGUE)}")
    return codes


# ==========================================
# Property Domain Models
# ==========================================

class PropertyBase(BaseModel):
    property_id: str = Field(..., description="Unique identifier e.g. PROP-GB-001")
    address: str
    postcode: str
    region: str
    property_type: str
    built_year: int
    total_floor_area_m2: float = Field(..., gt=0.0)
    baseline_value_gbp: float = Field(..., gt=0.0)

    # Current EPC & Energy
    current_epc_rating: str = Field(..., description="A, B, C, D, E, F, or G")
    current_epc_score: int = Field(..., ge=1, le=100)
    current_primary_energy_kwh_m2: float = Field(..., ge=0.0)

    # Fabric
    wall_type: str
    wall_insulation_status: str
    wall_u_value: float = Field(..., gt=0.0)
    roof_type: str
    roof_insulation_status: str
    roof_insulation_depth_mm: int = Field(..., ge=0)
    floor_type: str
    floor_insulation_status: str
    glazing_type: str

    # Heating & Renewables
    heating_type: str
    heating_efficiency_pct: float = Field(..., gt=0.0)
    annual_gas_kwh: float = Field(..., ge=0.0)
    annual_electricity_kwh: float = Field(..., ge=0.0)
    solar_pv_kwp: float = Field(default=0.0, ge=0.0)
    battery_storage_kwh: float = Field(default=0.0, ge=0.0)
    smart_controls: bool = False


class PropertyResponse(PropertyBase):
    model_config = ConfigDict(from_attributes=True)


class PropertyCreate(BaseModel):
    """
    What a homeowner enters to add a home. The server generates property_id and
    derives current_epc_rating (from the SAP score), primary energy intensity (from
    metered gas + electricity) and roof insulation status (from loft depth) when omitted.
    """
    address: str = Field(..., min_length=3, max_length=255)
    postcode: str = Field(..., min_length=2, max_length=16, pattern=r"^[A-Za-z0-9 ]+$")
    region: str = Field(..., min_length=2, max_length=64)
    property_type: str = Field(..., min_length=2, max_length=64)
    built_year: int = Field(..., ge=1500, le=2030)
    total_floor_area_m2: float = Field(..., gt=10.0, le=2000.0)
    baseline_value_gbp: float = Field(..., gt=10_000, le=50_000_000)

    current_epc_score: int = Field(..., ge=1, le=100, description="SAP score shown on the EPC")
    current_epc_rating: Optional[str] = Field(None, pattern=r"^[A-Ga-g]$")
    current_primary_energy_kwh_m2: Optional[float] = Field(None, ge=0.0, le=2000.0)

    wall_type: str = Field(..., min_length=2, max_length=64)
    wall_insulation_status: str = Field(..., pattern=r"^(Insulated|Uninsulated|Partial)$")
    wall_u_value: float = Field(..., ge=0.05, le=5.0)
    roof_type: str = Field("Pitched Tile", min_length=2, max_length=64)
    roof_insulation_status: Optional[str] = Field(None, pattern=r"^(Good|Partial|Uninsulated)$")
    roof_insulation_depth_mm: int = Field(..., ge=0, le=600)
    floor_type: str = Field(..., min_length=2, max_length=64)
    floor_insulation_status: str = Field(..., pattern=r"^(Insulated|Partial|Uninsulated)$")
    glazing_type: str = Field(..., min_length=2, max_length=64)

    heating_type: str = Field(..., min_length=2, max_length=64)
    heating_efficiency_pct: float = Field(..., ge=30.0, le=500.0, description="Boiler % or heat pump SCOP × 100")
    annual_gas_kwh: float = Field(..., ge=0.0, le=200_000)
    annual_electricity_kwh: float = Field(..., ge=0.0, le=100_000)
    solar_pv_kwp: float = Field(0.0, ge=0.0, le=50.0)
    battery_storage_kwh: float = Field(0.0, ge=0.0, le=100.0)
    smart_controls: bool = False


class PropertyUpdate(BaseModel):
    """Partial update: only the fields sent are changed (derived fields are recomputed)."""
    address: Optional[str] = Field(None, min_length=3, max_length=255)
    postcode: Optional[str] = Field(None, min_length=2, max_length=16, pattern=r"^[A-Za-z0-9 ]+$")
    region: Optional[str] = Field(None, min_length=2, max_length=64)
    property_type: Optional[str] = Field(None, min_length=2, max_length=64)
    built_year: Optional[int] = Field(None, ge=1500, le=2030)
    total_floor_area_m2: Optional[float] = Field(None, gt=10.0, le=2000.0)
    baseline_value_gbp: Optional[float] = Field(None, gt=10_000, le=50_000_000)
    current_epc_score: Optional[int] = Field(None, ge=1, le=100)
    current_epc_rating: Optional[str] = Field(None, pattern=r"^[A-Ga-g]$")
    current_primary_energy_kwh_m2: Optional[float] = Field(None, ge=0.0, le=2000.0)
    wall_type: Optional[str] = Field(None, min_length=2, max_length=64)
    wall_insulation_status: Optional[str] = Field(None, pattern=r"^(Insulated|Uninsulated|Partial)$")
    wall_u_value: Optional[float] = Field(None, ge=0.05, le=5.0)
    roof_type: Optional[str] = Field(None, min_length=2, max_length=64)
    roof_insulation_status: Optional[str] = Field(None, pattern=r"^(Good|Partial|Uninsulated)$")
    roof_insulation_depth_mm: Optional[int] = Field(None, ge=0, le=600)
    floor_type: Optional[str] = Field(None, min_length=2, max_length=64)
    floor_insulation_status: Optional[str] = Field(None, pattern=r"^(Insulated|Partial|Uninsulated)$")
    glazing_type: Optional[str] = Field(None, min_length=2, max_length=64)
    heating_type: Optional[str] = Field(None, min_length=2, max_length=64)
    heating_efficiency_pct: Optional[float] = Field(None, ge=30.0, le=500.0)
    annual_gas_kwh: Optional[float] = Field(None, ge=0.0, le=200_000)
    annual_electricity_kwh: Optional[float] = Field(None, ge=0.0, le=100_000)
    solar_pv_kwp: Optional[float] = Field(None, ge=0.0, le=50.0)
    battery_storage_kwh: Optional[float] = Field(None, ge=0.0, le=100.0)
    smart_controls: Optional[bool] = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_required_fields(cls, values):
        # Omission means "leave unchanged"; null is only meaningful for derived fields,
        # where it requests recalculation from the underlying property values.
        if isinstance(values, dict):
            derived = {"current_epc_rating", "current_primary_energy_kwh_m2", "roof_insulation_status"}
            null_fields = [key for key, value in values.items() if key in cls.model_fields and key not in derived and value is None]
            if null_fields:
                raise ValueError(f"Property fields cannot be null: {', '.join(sorted(null_fields))}")
        return values


class PropertySummary(BaseModel):
    property_id: str
    address: str
    postcode: str
    region: str
    property_type: str
    current_epc_rating: str
    current_epc_score: int
    baseline_value_gbp: float
    green_home_score: int = Field(..., ge=0, le=100)
    band: str
    flagged_for_intervention: bool


class PropertyRanking(BaseModel):
    """FR2: poor-performance flag, physical drivers and improvement potential."""
    rank: int
    property_id: str
    address: str
    region: str
    property_type: str
    current_epc_rating: str
    green_home_score: int
    band: str
    flagged_for_intervention: bool
    drivers: List[str] = Field(default_factory=list, description="Weakest score components, worst first")
    baseline_energy_kwh: float
    after_energy_kwh: float
    improvement_potential_pct: Optional[float] = Field(
        None, description="100 * (E_baseline - E_after) / E_baseline; null when baseline is unavailable"
    )
    delta_gas_kwh: float
    delta_electricity_kwh: float
    projected_score: int


# ==========================================
# 7-Component Green Home Score Schemas
# ==========================================

class ComponentScoreBreakdown(BaseModel):
    name: str
    weight: float
    sub_score: float = Field(..., ge=0.0, le=100.0)
    weighted_score: float
    description: str


class ScoreCalculationRequest(BaseModel):
    property_id: Optional[PropertyId] = None
    epc_score: int = Field(..., ge=1, le=100)
    primary_energy_kwh_m2: float = Field(..., ge=0.0, le=5000.0)
    wall_u_value: float = Field(..., gt=0.0, le=10.0)
    roof_insulation_depth_mm: int = Field(..., ge=0, le=1000)
    floor_insulated: bool = False
    heating_type: ShortText
    heating_efficiency_pct: float = Field(..., gt=0.0, le=1000.0)
    glazing_type: ShortText
    solar_pv_kwp: float = Field(default=0.0, ge=0.0, le=100.0)
    battery_storage_kwh: float = Field(default=0.0, ge=0.0, le=500.0)
    smart_controls: bool = False


class ScoreCalculationResponse(BaseModel):
    headline_score: int = Field(..., ge=0, le=100)
    band: str = Field(..., description="Highly Efficient, Good, Moderate, Poor, Very Poor")
    flagged_for_intervention: bool
    components: List[ComponentScoreBreakdown]
    monotonicity_guaranteed: bool = True


# ==========================================
# Retrofit DAG Schemas
# ==========================================

class RetrofitAction(BaseModel):
    code: str
    name: str
    category: str = Field(..., description="Fabric, Heating, Renewables, Controls")
    estimated_cost_gbp: float
    estimated_time_days: int
    description: str


class DAGSolveRequest(BaseModel):
    property_id: Optional[PropertyId] = None
    requested_interventions: ActionCodes = Field(
        ...,
        description="List of action codes e.g. ['heat_pump', 'solar_pv']"
    )
    current_installed_actions: ActionCodes = Field(
        default_factory=list,
        description="Actions already present in the home"
    )

    _requested = field_validator("requested_interventions")(_known_action_codes)
    _installed = field_validator("current_installed_actions")(_known_action_codes)


class TopologicalStage(BaseModel):
    stage_number: int
    stage_title: str
    actions: List[RetrofitAction]
    stage_rationale: str


class DAGSolveResponse(BaseModel):
    requested_actions: List[str]
    injected_prerequisites: List[str]
    full_resolved_action_codes: List[str]
    ordered_plan: List[TopologicalStage]
    fabric_first_enforced: bool = True
    total_estimated_cost_gbp: float


# ==========================================
# Decoupled Dual-Fuel Carbon Schemas
# ==========================================

class CarbonEvaluationRequest(BaseModel):
    baseline_gas_kwh: float = Field(..., ge=0.0, le=1_000_000)
    baseline_electricity_kwh: float = Field(..., ge=0.0, le=1_000_000)
    interventions: ActionCodes
    property_floor_area_m2: float = Field(..., gt=0.0, le=10_000)
    has_insulation: bool = False
    # Space-heating multiplier from the building-physics model (after / before, calibrated to
    # the meter). When omitted the engine falls back to its fixed per-measure catalogue values.
    fabric_reduction_factor: Optional[float] = Field(None, gt=0.0, le=1.0)

    _interventions = field_validator("interventions")(_known_action_codes)


class CarbonEvaluationResponse(BaseModel):
    baseline_gas_kwh: float
    baseline_electricity_kwh: float
    projected_gas_kwh: float
    projected_electricity_kwh: float
    delta_gas_kwh: float = Field(..., description="Elimination is negative")
    delta_electricity_kwh: float = Field(..., description="Import increase is positive")
    baseline_co2e_kg: float
    projected_co2e_kg: float
    net_co2e_reduction_kg: float
    net_co2e_reduction_tonnes: float
    net_co2e_reduction_pct: float
    
    # Financial running cost impact (£/year)
    baseline_annual_bill_gbp: float
    projected_annual_bill_gbp: float
    net_bill_savings_gbp: float
    
    # EPC Paradox resolution metrics
    heat_pump_paradox_detected: bool
    heat_pump_paradox_mitigated: bool
    paradox_explanation: str


# ==========================================
# Hedonic Property Uplift Schemas
# ==========================================

class UpliftEvaluationRequest(BaseModel):
    baseline_value_gbp: float = Field(..., gt=0.0, le=100_000_000)
    baseline_score: int = Field(..., ge=1, le=100)
    projected_score: int = Field(..., ge=1, le=100)
    floor_area_m2: float = Field(..., gt=0.0, le=10_000)
    region: ShortText
    property_type: ShortText


class UpliftEvaluationResponse(BaseModel):
    baseline_value_gbp: float
    baseline_score: int
    projected_score: int
    score_delta: int
    predicted_uplift_pct_min: float
    predicted_uplift_pct_max: float
    predicted_uplift_gbp_min: float
    predicted_uplift_gbp_max: float
    indicative_new_value_gbp_mid: float
    lloyds_green_mortgage_cashback_gbp: float
    lloyds_preferential_rate_discount_bps: int
    confidence_score: float


# ==========================================
# Full Retrofit Assessment Composite
# ==========================================

class FullRetrofitRequest(BaseModel):
    property_id: PropertyId
    selected_interventions: ActionCodes

    _selected = field_validator("selected_interventions")(_known_action_codes)


class PhysicsSummary(BaseModel):
    model: str
    heat_loss_w_per_k: float
    heat_loss_after_w_per_k: float
    heat_loss_breakdown: Dict[str, float]
    design_heat_loss_kw: float
    design_heat_loss_after_kw: float
    recommended_heat_pump_kw: float
    heating_need_before_kwh: float
    heating_need_after_kwh: float
    fabric_reduction_factor: float
    prebound_ratio: float
    calibrated_to_meter: bool
    realised_reduction_factor: float
    per_measure_saving_pct: Dict[str, float]


class FullRetrofitResponse(BaseModel):
    property_id: str
    address: str
    baseline_score: int
    projected_score: int
    score_delta: int
    band_transition: str
    flagged_for_intervention: bool
    dag_plan: DAGSolveResponse
    carbon_impact: CarbonEvaluationResponse
    property_uplift: UpliftEvaluationResponse
    total_retrofit_cost_gbp: float
    net_payback_years: Optional[float]
    already_installed: List[str] = Field(default_factory=list)
    baseline_band: Optional[str] = None
    projected_band: Optional[str] = None
    baseline_components: List[ComponentScoreBreakdown] = Field(default_factory=list)
    projected_components: List[ComponentScoreBreakdown] = Field(default_factory=list)
    baseline_drivers: List[str] = Field(default_factory=list)
    physics: Optional[PhysicsSummary] = None


# ==========================================
# Guarded LLM Scenario Schemas
# ==========================================

class ScenarioIntent(BaseModel):
    """Guarded parameter extraction schema - NO numerical calculations permitted."""
    detected_property_id: Optional[str] = None
    requested_interventions: List[str] = Field(
        default_factory=list,
        description="Validated retrofit codes from DAG catalogue"
    )
    user_primary_goal: Optional[str] = Field(
        default="carbon_and_bills",
        description="carbon_reduction, bill_savings, property_value, or compliance"
    )
    budget_constraint_gbp: Optional[float] = None


class GuardedScenarioChatRequest(BaseModel):
    user_query: str = Field(..., min_length=1, max_length=500)
    property_id: str = Field(..., min_length=1, max_length=64, description="One of the signed-in user's homes")


class GuardedScenarioChatResponse(BaseModel):
    user_query: str
    property_id: str
    extracted_intent: ScenarioIntent
    guardrail_validation_passed: bool
    audited_deterministic_metrics: FullRetrofitResponse
    natural_language_explanation: str
    lloyds_bank_advisory_note: str
    explanation_source: str = Field(
        default="template",
        description="'llm' when the model's wording passed numeric grounding, otherwise 'template'",
    )
    numeric_grounding_passed: bool = True
    within_budget: Optional[bool] = None


class AuditLogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_id: str
    property_id: Optional[str]
    query_text: str
    guardrail_status: str
    timestamp: Any


# ==========================================
# Auth & Saved Scenarios
# ==========================================

class AuthConfig(BaseModel):
    enabled: bool
    provider: str = "supabase"
    supabase_url: Optional[str] = None
    supabase_publishable_key: Optional[str] = None
    supabase_anon_key: Optional[str] = None


def _check_new_password(value: str) -> str:
    from backend.auth import password_problem

    problem = password_problem(value)
    if problem:
        raise ValueError(problem)
    return value


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254, pattern=r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
    password: str = Field(..., min_length=8, max_length=128)
    name: Optional[str] = Field(None, max_length=120, pattern=r"^[^<>]*$")

    _password = field_validator("password")(_check_new_password)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)

    _password = field_validator("new_password")(_check_new_password)


class AdminUserOut(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    is_active: bool
    is_superuser: bool
    created_at: Any = None
    last_login_at: Any = None
    homes: int = 0


class AdminUserUpdate(BaseModel):
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None


class AuthUserOut(BaseModel):
    is_superuser: bool = False
    id: str
    email: Optional[str] = None
    name: Optional[str] = None


class AuthSession(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: AuthUserOut


class SaveScenarioRequest(BaseModel):
    property_id: PropertyId
    name: str = Field(..., min_length=1, max_length=120)
    selected_interventions: ActionCodes

    _selected = field_validator("selected_interventions")(_known_action_codes)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Scenario name cannot be blank")
        return value


class SavedScenarioSummary(BaseModel):
    scenario_id: str
    name: str
    property_id: str
    address: str
    measures: List[str]
    baseline_score: int
    projected_score: int
    total_retrofit_cost_gbp: float
    net_co2e_reduction_tonnes: float
    created_at: Any


class SavedScenarioDetail(SavedScenarioSummary):
    result: FullRetrofitResponse
    assumptions: Dict[str, Any]

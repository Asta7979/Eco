import json
import uuid
from typing import List

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.orm import Session

from backend.api.deps import CurrentUser, SessionDep
from backend.auth import AuthUser
from backend.core.config import settings
from backend.db import database
from backend.engine.carbon_engine import UK_EF_ELEC, UK_EF_GAS, UK_UNIT_PRICE_ELEC_GBP, UK_UNIT_PRICE_GAS_GBP
from backend.schemas import (
    FullRetrofitRequest,
    FullRetrofitResponse,
    SavedScenarioDetail,
    SavedScenarioSummary,
    SaveScenarioRequest,
)
from backend.services.assessment_service import evaluate_full_retrofit_core

router = APIRouter(prefix="/scenarios", tags=["Saved Scenarios"])


def _summary(row) -> dict:
    result = FullRetrofitResponse.model_validate_json(row.result_json)
    return dict(
        scenario_id=row.scenario_id,
        name=row.name,
        property_id=row.property_id,
        address=result.address,
        measures=json.loads(row.measures_json),
        baseline_score=result.baseline_score,
        projected_score=result.projected_score,
        total_retrofit_cost_gbp=result.total_retrofit_cost_gbp,
        net_co2e_reduction_tonnes=result.carbon_impact.net_co2e_reduction_tonnes,
        created_at=row.created_at,
    )


def _owned_or_404(scenario_id: str, user: AuthUser, db: Session):
    S = database.SavedScenarioModel
    row = db.query(S).filter(S.scenario_id == scenario_id, S.user_id == user.id).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found.")
    return row


@router.post("", response_model=SavedScenarioSummary, status_code=201)
def save_scenario(req: SaveScenarioRequest, user: CurrentUser, db: SessionDep):
    """Runs the engines and saves an immutable snapshot of the result for the signed-in user."""
    result = evaluate_full_retrofit_core(
        FullRetrofitRequest(property_id=req.property_id, selected_interventions=req.selected_interventions),
        db,
        owner_uid=user.id,
    )
    row = database.SavedScenarioModel(
        scenario_id=uuid.uuid4().hex,
        user_id=user.id,
        user_email=user.email,
        property_id=req.property_id,
        name=req.name.strip(),
        measures_json=json.dumps(sorted(set(req.selected_interventions))),
        result_json=result.model_dump_json(),
        assumptions_json=json.dumps({
            "engine_version": settings.ENGINE_VERSION,
            "emission_factors_kg_co2e_per_kwh": {"gas": UK_EF_GAS, "electricity": UK_EF_ELEC},
            "emission_factor_source": "DESNZ/DEFRA GHG conversion factors",
            "unit_prices_gbp_per_kwh": {"gas": UK_UNIT_PRICE_GAS_GBP, "electricity": UK_UNIT_PRICE_ELEC_GBP},
        }),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _summary(row)


@router.get("", response_model=List[SavedScenarioSummary])
def list_scenarios(user: CurrentUser, db: SessionDep):
    """The signed-in user's saved scenarios, newest first."""
    S = database.SavedScenarioModel
    rows = db.query(S).filter(S.user_id == user.id).order_by(S.created_at.desc()).all()
    return [_summary(r) for r in rows]


@router.get("/{scenario_id}", response_model=SavedScenarioDetail)
def get_scenario(scenario_id: str, user: CurrentUser, db: SessionDep):
    row = _owned_or_404(scenario_id, user, db)
    return dict(
        **_summary(row),
        result=FullRetrofitResponse.model_validate_json(row.result_json),
        assumptions=json.loads(row.assumptions_json),
    )


@router.delete("/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: str, user: CurrentUser, db: SessionDep):
    db.delete(_owned_or_404(scenario_id, user, db))
    db.commit()

from fastapi import APIRouter, Query

from backend.api.deps import CurrentUser, SessionDep
from backend.engine.carbon_engine import calculate_decoupled_carbon_impact
from backend.engine.dag_engine import solve_retrofit_dag
from backend.engine.score_engine import calculate_green_home_score
from backend.engine.uplift_engine import predict_property_uplift
from backend.schemas import (
    CarbonEvaluationRequest,
    CarbonEvaluationResponse,
    DAGSolveRequest,
    DAGSolveResponse,
    FullRetrofitRequest,
    FullRetrofitResponse,
    ScoreCalculationRequest,
    ScoreCalculationResponse,
    UpliftEvaluationRequest,
    UpliftEvaluationResponse,
)
from backend.services.assessment_service import evaluate_full_retrofit_core, save_evaluation

router = APIRouter(prefix="/evaluate", tags=["Calculations"])


@router.post("/score", response_model=ScoreCalculationResponse)
def evaluate_score(req: ScoreCalculationRequest, user: CurrentUser):
    """Deterministic 7-component Green Home Score (0-100) with band classification."""
    return calculate_green_home_score(req)


@router.post("/dag-roadmap", response_model=DAGSolveResponse)
def evaluate_dag_roadmap(req: DAGSolveRequest, user: CurrentUser):
    """NetworkX topological retrofit sequence; injects missing fabric-first prerequisites."""
    return solve_retrofit_dag(req)


@router.post("/carbon", response_model=CarbonEvaluationResponse)
def evaluate_carbon(req: CarbonEvaluationRequest, user: CurrentUser):
    """Decoupled dual-fuel CO2e savings using UK GHG factors; detects the EPC heat pump paradox."""
    return calculate_decoupled_carbon_impact(req)


@router.post("/uplift", response_model=UpliftEvaluationResponse)
def evaluate_uplift(req: UpliftEvaluationRequest, user: CurrentUser):
    """Indicative property value range from the hedonic regressor, plus green finance incentives."""
    return predict_property_uplift(req)


@router.post("/full-retrofit", response_model=FullRetrofitResponse, tags=["Evaluations"])
def evaluate_full_retrofit(
    req: FullRetrofitRequest,
    user: CurrentUser,
    db: SessionDep,
    persist: bool = Query(True, description="Save the evaluation to retrofit_evaluations"),
):
    """
    End-to-end deterministic evaluation running all four engines in sequence.
    Interactive what-if previews should pass persist=false; saved scenarios keep the audit snapshot.
    """
    res = evaluate_full_retrofit_core(req, db, owner_uid=user.id)
    if persist:
        save_evaluation(db, res, user_id=user.id)
    return res

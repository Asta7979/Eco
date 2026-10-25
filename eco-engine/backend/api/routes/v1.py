"""
Versioned public API contracts (/api/v1), as in fastapi/full-stack-fastapi-template.

The handlers are the same tested functions that serve the unversioned routes, registered a
second time under stable names, so both surfaces always return identical results:

    POST /api/v1/score                       7-component Green Home Score
    POST /api/v1/retrofit                    full assessment: DAG plan, dual-fuel carbon, value, physics
    POST /api/v1/cv                          computer vision (facade / thermal / aerial)
    POST /api/v1/scenario                    guarded what-if advisor
    GET  /api/v1/physics/{property_id}       fast hourly building physics for one of your homes
    POST /api/v1/physics/{property_id}/detailed   ISO 52016 simulation (physics service)
"""

from typing import List

from fastapi import APIRouter, Body
from starlette.concurrency import run_in_threadpool

from backend.api.deps import CurrentUser, SessionDep
from backend.api.routes import advisor, evaluate, vision
from backend.engine.assessment import detect_installed_actions
from backend.engine.thermal_model import FABRIC_MEASURES, assess_physics
from backend.schemas import ActionCodes, PropertyCreate
from backend.services import remote
from backend.services.assessment_service import get_property_or_404, property_dict

router = APIRouter(prefix="/api/v1", tags=["API v1"])

router.add_api_route("/score", evaluate.evaluate_score, methods=["POST"], response_model=evaluate.ScoreCalculationResponse)
router.add_api_route("/retrofit", evaluate.evaluate_full_retrofit, methods=["POST"], response_model=evaluate.FullRetrofitResponse)
router.add_api_route("/cv", vision.vision_analyze, methods=["POST"])
router.add_api_route("/scenario", advisor.guarded_scenario_chat, methods=["POST"], response_model=advisor.GuardedScenarioChatResponse)


def _open_fabric_measures(prop: dict) -> List[str]:
    installed = set(detect_installed_actions(prop))
    return [m for m in FABRIC_MEASURES if m not in installed]


@router.get("/physics/{property_id}")
def physics(property_id: str, user: CurrentUser, db: SessionDep):
    """Heat loss, design load, heat-pump size and modelled savings for each open fabric measure."""
    prop = property_dict(get_property_or_404(property_id, db, user.id))
    from dataclasses import asdict

    return asdict(assess_physics(prop, _open_fabric_measures(prop)))


@router.post("/physics/{property_id}/detailed")
async def physics_detailed(
    property_id: str,
    user: CurrentUser,
    db: SessionDep,
    measures: ActionCodes = Body(default_factory=list, embed=True),
):
    """EN ISO 52016-1 hourly simulation in the physics microservice (pyBuildingEnergy)."""
    prop = property_dict(get_property_or_404(property_id, db, user.id))
    payload = {k: prop[k] for k in PropertyCreate.model_fields if k in prop}
    chosen = list(measures) or _open_fabric_measures(prop)
    return await run_in_threadpool(remote.physics_detailed, payload, chosen)

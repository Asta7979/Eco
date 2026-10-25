from typing import List, Optional

from fastapi import APIRouter, Query, Response, status

from backend.api.deps import CurrentUser, SessionDep
from backend.db import database
from backend.engine.assessment import detect_installed_actions
from backend.engine.dag_engine import RETROFIT_ACTIONS_CATALOGUE
from backend.engine.epc_predictor import epc_predictor
from backend.schemas import (
    PropertyCreate,
    PropertyRanking,
    PropertyResponse,
    PropertySummary,
    PropertyUpdate,
    RetrofitAction,
)
from backend.services.assessment_service import (
    delete_property_cascade,
    derive_fields,
    get_property_or_404,
    new_property_id,
    portfolio_cache,
    property_dict,
)

router = APIRouter(tags=["Properties"])


@router.get("/catalogue", response_model=List[RetrofitAction], tags=["Calculations"])
def list_retrofit_catalogue():
    """Retrofit measures the DAG planner can schedule, with indicative cost and duration."""
    return list(RETROFIT_ACTIONS_CATALOGUE.values())


@router.get("/properties", response_model=List[PropertySummary])
def list_properties(
    user: CurrentUser,
    db: SessionDep,
    region: Optional[str] = Query(None, description="Filter by UK region"),
    epc_band: Optional[str] = Query(None, description="Filter by EPC band"),
):
    """The signed-in user's homes with Green Home Score, band and intervention flag."""
    return portfolio_cache.summaries(db, user.id, region=region, epc_band=epc_band)


@router.post("/properties", response_model=PropertyResponse, status_code=status.HTTP_201_CREATED)
def create_property(req: PropertyCreate, user: CurrentUser, db: SessionDep):
    """
    Adds a home to the signed-in user's portfolio. The EPC band, primary energy intensity
    and roof insulation status are derived when not supplied.
    """
    values = derive_fields(req.model_dump(), explicit=req.model_fields_set)
    row = database.PropertyModel(property_id=new_property_id(), owner_uid=user.id, **values)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/properties/ranking", response_model=List[PropertyRanking])
def rank_properties(
    user: CurrentUser,
    db: SessionDep,
    flagged_only: bool = Query(False, description="Only Poor / Very Poor homes"),
):
    """
    FR2: flags Poor / Very Poor homes, lists their physical drivers, and ranks the user's
    homes by improvement potential under the whole-home package:
    100 * (E_baseline - E_after) / E_baseline, with gas and electricity reported separately.
    """
    return portfolio_cache.ranking(db, user.id, flagged_only=flagged_only)


@router.get("/properties/{property_id}", response_model=PropertyResponse)
def get_property_detail(property_id: str, user: CurrentUser, db: SessionDep):
    """Full building specification for one of the user's homes."""
    return get_property_or_404(property_id, db, user.id)


@router.patch("/properties/{property_id}", response_model=PropertyResponse)
def update_property(property_id: str, req: PropertyUpdate, user: CurrentUser, db: SessionDep):
    """Updates the fields sent; derived fields (EPC band, intensity, roof status) are recomputed."""
    row = get_property_or_404(property_id, db, user.id)
    changes = req.model_dump(exclude_unset=True)
    merged = {**property_dict(row), **changes}
    # Recompute derived values unless the user set them in this request.
    explicit = set(changes)
    derived = derive_fields(merged, explicit=explicit)
    for field in PropertyCreate.model_fields:
        if field in derived:
            setattr(row, field, derived[field])
    db.commit()
    db.refresh(row)
    return row


@router.delete("/properties/{property_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_property(property_id: str, user: CurrentUser, db: SessionDep):
    """Deletes a home with its evaluations, saved scenarios and advisor logs."""
    delete_property_cascade(db, get_property_or_404(property_id, db, user.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/properties/{property_id}/epc-prediction")
def predict_epc_potential(property_id: str, user: CurrentUser, db: SessionDep):
    """
    Second opinion from an external ML model (XGBoost trained on the EPC register,
    b-rasagna/EPC-Energy-Rating-Prediction): the home's predicted *potential* EPC band, with
    class probabilities and a list of inputs that had to be assumed. Indicative only.
    """
    prop = property_dict(get_property_or_404(property_id, db, user.id))
    open_measures = len(set(RETROFIT_ACTIONS_CATALOGUE) - set(detect_installed_actions(prop)))
    return {"property_id": property_id, **epc_predictor.predict(prop, open_measures)}

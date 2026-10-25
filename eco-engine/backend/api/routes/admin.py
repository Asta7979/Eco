"""
Admin-only account management (pattern from full-stack-fastapi-template's users routes).
Every route depends on CurrentSuperuser, so non-admins get 403. Admins cannot deactivate,
demote or delete themselves, so the site can't be left without an administrator by mistake.
"""

from typing import List

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func

from backend.api.deps import CurrentSuperuser, SessionDep
from backend.auth import superuser_emails
from backend.db import database
from backend.schemas import AdminUserOut, AdminUserUpdate
from backend.services.assessment_service import delete_property_cascade

router = APIRouter(prefix="/admin", tags=["Admin"])


def _out(row, homes: int) -> AdminUserOut:
    return AdminUserOut(
        id=row.user_id,
        email=row.email,
        name=row.name,
        is_active=bool(row.is_active),
        is_superuser=bool(row.is_superuser) or row.email.lower() in superuser_emails(),
        created_at=row.created_at,
        last_login_at=row.last_login_at,
        homes=homes,
    )


def _homes_by_owner(db) -> dict:
    P = database.PropertyModel
    return dict(db.query(P.owner_uid, func.count(P.property_id)).group_by(P.owner_uid).all())


@router.get("/users", response_model=List[AdminUserOut])
def list_users(
    admin: CurrentSuperuser,
    db: SessionDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Local accounts, newest first, with how many homes each owns."""
    U = database.UserModel
    rows = db.query(U).order_by(U.created_at.desc()).offset(skip).limit(limit).all()
    homes = _homes_by_owner(db)
    return [_out(r, homes.get(r.user_id, 0)) for r in rows]


@router.get("/stats")
def admin_stats(admin: CurrentSuperuser, db: SessionDep):
    U = database.UserModel
    return {
        "users": db.query(U).count(),
        "active_users": db.query(U).filter(U.is_active.is_(True)).count(),
        "homes": db.query(database.PropertyModel).count(),
        "saved_scenarios": db.query(database.SavedScenarioModel).count(),
    }


def _target(db, user_id: str, admin) -> "database.UserModel":
    row = db.get(database.UserModel, user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    return row


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_user(user_id: str, req: AdminUserUpdate, admin: CurrentSuperuser, db: SessionDep):
    """Activate / deactivate an account or change its admin role."""
    row = _target(db, user_id, admin)
    if user_id == admin.id and (req.is_active is False or req.is_superuser is False):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't deactivate or demote your own account.")
    if req.is_active is not None:
        row.is_active = req.is_active
    if req.is_superuser is not None:
        row.is_superuser = req.is_superuser
    db.commit()
    return _out(row, _homes_by_owner(db).get(row.user_id, 0))


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, admin: CurrentSuperuser, db: SessionDep):
    """Deletes an account with all of its homes, scenarios, evaluations and advisor logs."""
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Use 'Delete my account' to remove your own account.")
    row = _target(db, user_id, admin)
    P = database.PropertyModel
    for prop in db.query(P).filter(P.owner_uid == user_id).all():
        delete_property_cascade(db, prop)
    for model in (database.SavedScenarioModel, database.AuditLogModel, database.RetrofitEvaluationModel):
        db.query(model).filter_by(user_id=user_id).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

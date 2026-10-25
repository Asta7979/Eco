"""
Shared FastAPI dependencies (pattern from full-stack-fastapi-template: SessionDep /
CurrentUser type aliases so routes declare what they need in one word).
"""

from typing import Annotated, Generator

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.auth import AuthUser, get_current_user
from backend.db import database


def get_db() -> Generator[Session, None, None]:
    # Resolve SessionLocal at call time so a reloaded database module (tests) is honoured.
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


SessionDep = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[AuthUser, Depends(get_current_user)]


def get_current_superuser(user: CurrentUser) -> AuthUser:
    """Admin-only routes (template's get_current_active_superuser): 403 for everyone else."""
    if not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have permission to do that.")
    return user


CurrentSuperuser = Annotated[AuthUser, Depends(get_current_superuser)]

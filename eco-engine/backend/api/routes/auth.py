import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status

from backend.api.deps import CurrentUser, SessionDep
from backend.auth import (
    LOCAL_TOKEN_TTL_S,
    AuthUser,
    auth_provider,
    hash_password,
    issue_local_token,
    local_auth_enabled,
    LoginThrottle,
    login_throttle,
    superuser_emails,
    supabase_settings,
    verify_password,
    verify_password_or_dummy,
)

# Separate per-IP budget: stops one client cycling through many emails.
login_ip_throttle = LoginThrottle(limit=20, window_s=300.0)
from backend.db import database
from backend.schemas import AuthConfig, AuthSession, AuthUserOut, LoginRequest, PasswordChangeRequest, RegisterRequest

router = APIRouter(prefix="/auth", tags=["Auth"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("/config", response_model=AuthConfig)
def auth_config():
    """Which sign-in provider the browser should use. Publishable keys are public."""
    provider = auth_provider()
    if provider is None:
        return AuthConfig(enabled=False, provider="none")
    if provider == "local":
        return AuthConfig(enabled=True, provider="local")
    url, key = supabase_settings()
    return AuthConfig(
        enabled=True, provider="supabase", supabase_url=url,
        supabase_publishable_key=key, supabase_anon_key=key,
    )


def _require_local_auth() -> None:
    if not local_auth_enabled():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Local accounts are not enabled on this server.")


def _session_for(row) -> AuthSession:
    is_admin = bool(row.is_superuser) or (row.email or "").lower() in superuser_emails()
    user = AuthUser(id=row.user_id, email=row.email, name=row.name, is_superuser=is_admin)
    return AuthSession(
        access_token=issue_local_token(user, row.password_changed_at),
        expires_in=LOCAL_TOKEN_TTL_S,
        user=AuthUserOut(id=user.id, email=user.email, name=user.name, is_superuser=is_admin),
    )


@router.post("/register", response_model=AuthSession, status_code=201)
def auth_register(req: RegisterRequest, db: SessionDep):
    """Creates a local account (bcrypt-hashed password) and returns a signed session token."""
    _require_local_auth()
    U = database.UserModel
    email = req.email.strip().lower()
    if db.query(U).filter(U.email == email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists. Sign in instead.")
    row = U(
        user_id=uuid.uuid4().hex,
        email=email,
        name=(req.name or "").strip() or None,
        password_hash=hash_password(req.password),
        last_login_at=_utcnow(),
    )
    db.add(row)
    db.commit()
    return _session_for(row)


@router.post("/login", response_model=AuthSession)
def auth_login(req: LoginRequest, request: Request, db: SessionDep):
    """
    Signs in to a local account. Failures are throttled per email and per client IP, the
    response time does not reveal whether the email exists, and deactivated accounts are refused.
    """
    from backend.core.security import client_ip

    _require_local_auth()
    U = database.UserModel
    email = req.email.strip().lower()
    ip_key = f"ip:{client_ip(request.scope)}"
    login_throttle.check(email)
    login_ip_throttle.check(ip_key)
    row = db.query(U).filter(U.email == email).first()
    if not verify_password_or_dummy(req.password, row.password_hash if row else None):
        login_throttle.fail(email)
        login_ip_throttle.fail(ip_key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That email and password don't match.")
    if not row.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been deactivated.")
    login_throttle.reset(email)
    row.last_login_at = _utcnow()
    db.commit()
    return _session_for(row)


@router.get("/me", response_model=AuthUserOut)
def auth_me(user: CurrentUser):
    return AuthUserOut(id=user.id, email=user.email, name=user.name, is_superuser=user.is_superuser)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(req: PasswordChangeRequest, user: CurrentUser, db: SessionDep):
    """
    Changes a local account's password. Every session issued before the change (on any
    device) stops working; the caller signs in again with the new password.
    """
    _require_local_auth()
    row = db.get(database.UserModel, user.id)
    if row is None or not verify_password(req.current_password, row.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your current password is incorrect.")
    if verify_password(req.new_password, row.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a password you haven't used here.")
    row.password_hash = hash_password(req.new_password)
    row.password_changed_at = _utcnow()
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(user: CurrentUser, db: SessionDep):
    """
    Deletes the signed-in user's homes, evaluations, saved scenarios and advisor logs, and
    (for local accounts) the account itself. Supabase accounts are removed in Supabase.
    """
    from backend.services.assessment_service import delete_property_cascade

    P = database.PropertyModel
    for prop in db.query(P).filter(P.owner_uid == user.id).all():
        delete_property_cascade(db, prop)
    db.query(database.SavedScenarioModel).filter_by(user_id=user.id).delete(synchronize_session=False)
    db.query(database.AuditLogModel).filter_by(user_id=user.id).delete(synchronize_session=False)
    db.query(database.RetrofitEvaluationModel).filter_by(user_id=user.id).delete(synchronize_session=False)
    db.query(database.UserModel).filter_by(user_id=user.id).delete(synchronize_session=False)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

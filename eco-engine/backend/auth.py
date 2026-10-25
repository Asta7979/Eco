"""
Authentication for Project Eco.

Two providers, chosen at request time:

* supabase  when SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are set. The browser signs in
            with supabase-js and the backend verifies each bearer token with
            Supabase Auth (GET /auth/v1/user).
* local     otherwise (the default). Email + password accounts are stored in the
            app database with bcrypt hashes, and the API issues signed HS256 JWTs.
            Works offline with no external keys. Set ECO_LOCAL_AUTH=0 to turn it off.

Environment:
    SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY   switch to Supabase Auth
    SUPABASE_ANON_KEY                         legacy key fallback
    ECO_AUTH_SECRET                   JWT signing secret for local auth; when unset a
                                      random one is created once in .auth_secret
    ECO_LOCAL_AUTH                    "0" disables local accounts (guest mode only)
"""

import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import bcrypt
import httpx
import jwt
from fastapi import Header, HTTPException, status

_CACHE_TTL_S = 60.0
_token_cache: Dict[str, Tuple[float, "AuthUser"]] = {}

LOCAL_TOKEN_TTL_S = 7 * 24 * 3600
_SECRET_FILE = Path(__file__).resolve().parent.parent / ".auth_secret"
_JWT_ISSUER = "eco-engine"


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: Optional[str]
    name: Optional[str] = None
    is_superuser: bool = False


BCRYPT_MAX_BYTES = 72  # bcrypt ignores (bcrypt>=5: rejects) anything longer


def password_problem(password: str) -> Optional[str]:
    """Why a new password is unacceptable, or None. Shared by registration and password change."""
    if len(password) < 8:
        return "Use at least 8 characters."
    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        return "Use at most 72 bytes (about 72 letters, fewer with accents or emoji)."
    if password.strip() != password or not password.strip():
        return "Don't start or end the password with spaces."
    return None


def superuser_emails() -> set:
    """Bootstrap admins (ECO_SUPERUSER_EMAILS, comma-separated): works for local and Supabase accounts."""
    return {e.strip().lower() for e in os.getenv("ECO_SUPERUSER_EMAILS", "").split(",") if e.strip()}


def supabase_settings() -> Tuple[Optional[str], Optional[str]]:
    # Users often copy the Data API URL; Auth and the JS client need the project root.
    url = (os.getenv("SUPABASE_URL") or "").rstrip("/").removesuffix("/rest/v1") or None
    key = os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or None
    return url, key


def supabase_enabled() -> bool:
    url, key = supabase_settings()
    return bool(url and key)


def local_auth_enabled() -> bool:
    return not supabase_enabled() and os.getenv("ECO_LOCAL_AUTH", "1") != "0"


def auth_provider() -> Optional[str]:
    if supabase_enabled():
        return "supabase"
    if local_auth_enabled():
        return "local"
    return None


def auth_enabled() -> bool:
    return auth_provider() is not None


def validate_auth_configuration() -> None:
    """Fail at startup when production auth cannot issue stable, valid sessions."""
    url, key = supabase_settings()
    if bool(url) != bool(key):
        raise RuntimeError("Set SUPABASE_URL and a publishable (or legacy anon) key together")
    if key and key.startswith("sb_secret_"):
        raise RuntimeError("Use a Supabase publishable key in the browser, not a secret key")
    if local_auth_enabled() and os.getenv("ENVIRONMENT") == "production":
        secret = os.getenv("ECO_AUTH_SECRET", "")
        if len(secret) < 32:
            raise RuntimeError("ECO_AUTH_SECRET must contain at least 32 characters in production")


# ---------- Local accounts ----------

def _jwt_secret() -> str:
    env = os.getenv("ECO_AUTH_SECRET")
    if env:
        return env
    try:
        existing = _SECRET_FILE.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    fresh = secrets.token_urlsafe(48)
    try:
        _SECRET_FILE.write_text(fresh, encoding="utf-8")
    except OSError:
        pass  # read-only filesystem: tokens last until restart
    return fresh


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


# Compared against when the email is unknown, so a failed sign-in takes the same time
# whether or not the account exists (no user enumeration by timing).
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser-not-a-password", bcrypt.gensalt(rounds=12)).decode()


def verify_password_or_dummy(password: str, password_hash: Optional[str]) -> bool:
    if password_hash is None:
        verify_password(password, _DUMMY_HASH)
        return False
    return verify_password(password, password_hash)


def _password_version(changed_at) -> Optional[str]:
    """Exact database password-change marker included in local sessions."""
    return changed_at.isoformat(timespec="microseconds") if changed_at is not None else None


def issue_local_token(user: AuthUser, password_changed_at=None) -> str:
    now = int(time.time())
    payload = {
        "sub": user.id,
        "email": user.email,
        "name": user.name,
        "iss": _JWT_ISSUER,
        "iat": now,
        "exp": now + LOCAL_TOKEN_TTL_S,
        "pwdv": _password_version(password_changed_at),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def _verify_local(token: str) -> AuthUser:
    try:
        data = jwt.decode(token, _jwt_secret(), algorithms=["HS256"], issuer=_JWT_ISSUER)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired. Please sign in again.")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session invalid. Please sign in again.")
    # A valid signature does not mean the account still exists. Checking the row
    # makes account deletion revoke outstanding local sessions immediately.
    from backend.db import database

    with database.SessionLocal() as db:
        row = db.get(database.UserModel, data["sub"])
        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session invalid. Please sign in again.")
        if not row.is_active:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been deactivated.")
        if data.get("pwdv") != _password_version(row.password_changed_at):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your password was changed. Please sign in again.")
        return AuthUser(
            id=row.user_id,
            email=row.email,
            name=row.name,
            is_superuser=bool(row.is_superuser) or (row.email or "").lower() in superuser_emails(),
        )


class LoginThrottle:
    """At most `limit` failed sign-ins per key (email, and separately IP) in a rolling window."""

    MAX_KEYS = 20_000  # bounded memory even under a flood of random emails

    def __init__(self, limit: int = 5, window_s: float = 300.0):
        self.limit, self.window_s = limit, window_s
        self._fails: Dict[str, list] = {}

    def check(self, key: str) -> None:
        if len(self._fails) > self.MAX_KEYS:
            now = time.monotonic()
            self._fails = {k: v for k, v in self._fails.items() if v and now - v[-1] < self.window_s}
        now = time.monotonic()
        recent = [t for t in self._fails.get(key, []) if now - t < self.window_s]
        self._fails[key] = recent
        if len(recent) >= self.limit:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many attempts. Wait a few minutes and try again.",
            )

    def fail(self, key: str) -> None:
        self._fails.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)


login_throttle = LoginThrottle()


# ---------- Supabase ----------

def _verify_with_supabase(token: str) -> AuthUser:
    url, key = supabase_settings()
    try:
        resp = httpx.get(
            f"{url}/auth/v1/user",
            headers={"apikey": key, "Authorization": f"Bearer {token}"},
            timeout=5.0,
        )
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication service unreachable.")
    if resp.status_code != 200:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired or invalid. Please sign in again.")
    data = resp.json()
    email = data.get("email")
    return AuthUser(id=data["id"], email=email, is_superuser=(email or "").lower() in superuser_emails())


def get_current_user(authorization: Optional[str] = Header(None)) -> AuthUser:
    """FastAPI dependency: the signed-in user, or 401."""
    provider = auth_provider()
    if provider is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is turned off on this server.")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to use saved scenarios.")
    token = authorization.split(" ", 1)[1].strip()

    if provider == "local":
        return _verify_local(token)

    now = time.monotonic()
    cached = _token_cache.get(token)
    if cached and cached[0] > now:
        return cached[1]

    user = _verify_with_supabase(token)
    _token_cache[token] = (now + _CACHE_TTL_S, user)
    if len(_token_cache) > 1000:
        for t in [t for t, (exp, _) in _token_cache.items() if exp <= now]:
            _token_cache.pop(t, None)
    return user

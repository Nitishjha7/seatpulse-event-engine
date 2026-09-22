"""
Core authentication logic: password hashing, JWT tokens, and current-user dependency.

Token strategy:

  ACCESS TOKEN   30 min   -> Returned in JSON, stored in frontend RAM.
  REFRESH TOKEN  7 days   -> Stored in httpOnly cookie, inaccessible to JavaScript.

Rationale:
  - localStorage exposes tokens to XSS (via npm packages or injected scripts);
    httpOnly cookies are inaccessible to JS.
  - Cookies on every request risk CSRF, so the access token authorizes via
    the Authorization header instead (not auto-sent in CSRF scenarios), and
    the cookie is only used to obtain a new access token.
  - Short-lived access tokens limit the blast radius if one leaks.

Refresh token revocation:
  Each refresh token carries a `jti` whitelisted in Redis. On logout the ID
  is removed from Redis, invalidating the token immediately instead of
  leaving it valid for 7 more days on JWT expiry alone.
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from core.config import settings
from core.database import get_db
from core.models import User
from core.redis_client import redis_client

REFRESH_COOKIE_NAME = "seatpulse_refresh"

# auto_error=False so FastAPI doesn't raise 403 automatically;
# lets us return custom 401 messages instead.
_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """
    bcrypt is deliberately slow. SHA256 would allow millions of guesses per
    second; bcrypt takes ~100ms per hash, making brute force infeasible.
    Salting is handled automatically.
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        # User created via Google; no password exists.
        return False
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def _create_token(payload: dict, expires: timedelta, token_type: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            **payload,
            "type": token_type,   # Prevent access tokens from being used as refresh tokens
            "iat": now,
            "exp": now + expires,
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


def create_access_token(user_id: int) -> str:
    return _create_token(
        {"sub": str(user_id)},
        timedelta(minutes=settings.ACCESS_TOKEN_MINUTES),
        "access",
    )


def create_refresh_token(user_id: int) -> str:
    """
    Generate a refresh token and whitelist its ID in Redis.

    Redis key TTL matches the token expiry, so cleanup is automatic and
    doesn't need a separate maintenance job.
    """
    jti = uuid.uuid4().hex
    token = _create_token(
        {"sub": str(user_id), "jti": jti},
        timedelta(days=settings.REFRESH_TOKEN_DAYS),
        "refresh",
    )
    redis_client.setex(
        _refresh_key(user_id, jti),
        timedelta(days=settings.REFRESH_TOKEN_DAYS),
        "1",
    )
    return token


def _refresh_key(user_id: int, jti: str) -> str:
    return f"refresh:{user_id}:{jti}"


def decode_token(token: str, expected_type: str) -> dict:
    """Verify token, raising 401 on failure. jwt.decode() validates signature and expiry."""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token is invalid")

    if payload.get("type") != expected_type:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect token type")

    return payload


def revoke_refresh_token(user_id: int, jti: str) -> None:
    redis_client.delete(_refresh_key(user_id, jti))


def refresh_token_is_valid(user_id: int, jti: str) -> bool:
    return bool(redis_client.exists(_refresh_key(user_id, jti)))


def revoke_all_refresh_tokens(user_id: int) -> int:
    """Logout from all devices. Should be called on password changes."""
    keys = list(redis_client.scan_iter(f"refresh:{user_id}:*"))
    return redis_client.delete(*keys) if keys else 0


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Extracts the user for protected routes. `user_id` comes from the token, not the body, so it can't be spoofed."""
    if creds is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(creds.credentials, "access")
    user = db.get(User, int(payload["sub"]))

    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")

    return user


def require_role(*roles: str):
    """
    Restrict access to specific roles.

    Use:
        @router.post("", dependencies=[Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN))])

    Returns 403, not 404 — unlike IDOR cases where 404 hides resource
    existence, these endpoints are public knowledge; the user just lacks
    the permission.
    """

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"This action requires the {' or '.join(roles)} role",
            )
        return user

    return dependency


def get_current_user_optional(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User | None:
    """Returns the user if logged in, otherwise None."""
    if creds is None:
        return None
    try:
        payload = decode_token(creds.credentials, "access")
        return db.get(User, int(payload["sub"]))
    except HTTPException:
        return None


def user_from_ws_token(token: str | None, db: Session) -> User | None:
    """
    WebSocket auth via query parameter, since browser WebSocket APIs don't
    support custom headers — hence `?token=...`.

    Trade-off: URLs can end up in server logs, so only short-lived access
    tokens are accepted here; refresh tokens are never used.
    """
    if not token:
        return None
    try:
        payload = decode_token(token, "access")
        return db.get(User, int(payload["sub"]))
    except HTTPException:
        return None


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

def set_refresh_cookie(response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,      # Prevents JS access (XSS protection)
        secure=settings.COOKIE_SECURE,   # False in dev (HTTP)
        samesite="lax",     # Prevents cross-site POST (CSRF protection)
        max_age=settings.REFRESH_TOKEN_DAYS * 24 * 3600,
        path="/api/auth",   # Scoped to auth routes
    )


def clear_refresh_cookie(response) -> None:
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/auth")


def read_refresh_cookie(request: Request) -> str | None:
    return request.cookies.get(REFRESH_COOKIE_NAME)


def random_state() -> str:
    """Generates a random string for OAuth CSRF protection."""
    return secrets.token_urlsafe(24)

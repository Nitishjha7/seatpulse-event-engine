"""
Core authentication logic: password hashing, JWT tokens, and current-user dependency.

Token strategy (design decision, often discussed in interviews):

  ACCESS TOKEN   30 min   -> Returned in JSON, stored in frontend RAM.
  REFRESH TOKEN  7 days   -> Stored in httpOnly cookie, inaccessible to JavaScript.

Rationale:
  - Storing tokens in localStorage exposes them to XSS (via npm packages or injected scripts). httpOnly cookies are inaccessible to JS.
  - Using cookies for every request risks CSRF. Therefore, the access token handles authorization via the Authorization header (which is not automatically sent in CSRF scenarios), while the cookie is used solely to obtain a new access token.
  - Access tokens are short-lived, limiting the impact if compromised.

Refresh token revocation:
  Each refresh token contains a `jti` (unique ID) whitelisted in Redis. On logout, the ID is removed from Redis, immediately invalidating the token. Relying solely on JWT expiry would leave tokens active for 7 days post-logout.
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import User
from redis_client import redis_client

REFRESH_COOKIE_NAME = "seatpulse_refresh"

# auto_error=False: Prevent FastAPI from raising 403 automatically;
# allows us to return custom 401 messages.
_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """
    bcrypt: A deliberately slow hashing algorithm.

    Fast hashes like SHA256 are unsuitable here as they allow millions of
    guesses per second. bcrypt takes ~100ms per hash, making brute force
    computationally infeasible. Salts are handled automatically.
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

    The Redis key TTL matches the token expiry, ensuring automatic cleanup
    without requiring a separate maintenance job.
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
    """
    Verify token. Raises 401 on failure.

    jwt.decode() automatically validates the signature and expiry.
    """
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expire ho gaya")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token invalid hai")

    if payload.get("type") != expected_type:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Galat token type")

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
    """
    Extracts the user for protected routes.

    ⭐ `user_id` is derived from the token, not the request body, preventing
    ID spoofing.
    """
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

    ⚠️ Returns 403 Forbidden. Unlike IDOR cases where 404 is used to hide
    resource existence, these endpoints are public knowledge; the user simply
    lacks the required permissions.
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
    WebSocket authentication via query parameter.

    WebSocket handshakes do not support custom headers in browser APIs,
    necessitating the use of `?token=...`.

    Trade-off: URLs may appear in server logs. Only short-lived access tokens
    are permitted here; refresh tokens are never used.
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

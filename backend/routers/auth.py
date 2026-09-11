"""
Auth routes: signup, login, refresh, logout, and Google OAuth.

Google OAuth flow (Authorization Code):

  1. User clicks "Continue with Google"
     -> Browser navigates to backend /google/login

  2. Backend redirects user to Google with a random `state` parameter.

  3. User authenticates with Google and grants permissions.

  4. Google redirects user back to /google/callback with an authorization `code`.

  5. ⭐ BACKEND exchanges the code for user info using `client_secret`.
     This is a server-to-server operation; the browser is not involved.

  6. Backend sets a refresh cookie and redirects the user to the frontend.

This "Authorization Code" flow is used instead of the legacy "Implicit" flow,
which exposed tokens in URLs, risking leakage via browser history and server logs.

The `client_secret` remains exclusively on the backend. In frontend-only OAuth,
the secret would be exposed in the browser, creating a security vulnerability.
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import (
    clear_refresh_cookie,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    random_state,
    read_refresh_cookie,
    refresh_token_is_valid,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    set_refresh_cookie,
    verify_password,
)
from config import settings
from database import get_db
from models import User
from rate_limit import LOGIN_FAIL, REGISTER, check, client_ip, enforce
from redis_client import redis_client
from schemas import (
    AuthConfigOut,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

# OAuth state expires in 10 minutes.
STATE_TTL_SECONDS = 600


def _to_user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        avatar_url=user.avatar_url,
        role=user.role,
        is_google_user=user.google_id is not None,
    )


def _issue_tokens(response: Response, user: User) -> TokenResponse:
    """Returns access token in JSON and refresh token in an httpOnly cookie."""
    set_refresh_cookie(response, create_refresh_token(user.id))
    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in=settings.ACCESS_TOKEN_MINUTES * 60,
        user=_to_user_out(user),
    )


# ---------------------------------------------------------------------------
# Email + password
# ---------------------------------------------------------------------------

@router.get("/config", response_model=AuthConfigOut)
def auth_config():
    """Determines if the Google login button should be displayed."""
    return AuthConfigOut(
        google_enabled=settings.google_enabled,
        ai_search_enabled=settings.ai_search_enabled,
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Registers a new account and logs the user in immediately."""
    # Prevent account farming by limiting registration attempts per IP.
    enforce(response, f"register:{client_ip(request)}", REGISTER)

    user = User(
        email=payload.email.lower(),
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
    )
    db.add(user)

    try:
        db.commit()
    except IntegrityError:
        # Rely on DB unique constraints to handle race conditions during concurrent signups.
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email is already registered")

    db.refresh(user)
    return _issue_tokens(response, user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    email = payload.email.lower()

    # ---- Brute force protection ----
    # ⭐ Rate limiting is applied to the EMAIL, not the IP:
    #
    #   1. IP-based limits block legitimate users sharing a NAT (e.g., offices).
    #   2. Email-based limits are more targeted against attackers.
    #
    # Only failed attempts consume the rate limit budget.
    bucket = f"login:{email}"
    allowed, _, retry_after = check(bucket, LOGIN_FAIL, cost=0)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed attempts — please try again later",
            headers={"Retry-After": str(retry_after)},
        )

    user = db.scalar(select(User).where(User.email == email))

    # ⚠️ Commit immediately after reading to release the DB transaction.
    #
    # Bcrypt hashing is CPU-intensive (~100ms). Holding the transaction open
    # during this time causes "idle in transaction" connection pool exhaustion.
    db.commit()

    # ⚠️ Use a generic error message to prevent user enumeration.
    if user is None or not verify_password(payload.password, user.hashed_password):
        # Only increment failure count on incorrect credentials.
        check(bucket, LOGIN_FAIL, cost=1)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")

    return _issue_tokens(response, user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    """
    Refreshes the access token using the refresh cookie.

    ⭐ ROTATION: Revoke the old refresh token and issue a new one.
    This mitigates token theft; if an attacker uses a stolen token, the
    legitimate user's session is invalidated, exposing the breach.
    """
    token = read_refresh_cookie(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token missing")

    payload = decode_token(token, "refresh")
    user_id, jti = int(payload["sub"]), payload["jti"]

    # Redis whitelist check ensures tokens are invalidated upon logout.
    if not refresh_token_is_valid(user_id, jti):
        clear_refresh_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token has been revoked")

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        clear_refresh_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")

    revoke_refresh_token(user_id, jti)
    return _issue_tokens(response, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response):
    """
    Logs out the user by revoking the refresh token in Redis and clearing the cookie.

    Access tokens remain valid until their short-lived expiration.
    """
    token = read_refresh_cookie(request)
    if token:
        try:
            payload = decode_token(token, "refresh")
            revoke_refresh_token(int(payload["sub"]), payload["jti"])
        except HTTPException:
            pass

    clear_refresh_cookie(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(response: Response, user: User = Depends(get_current_user)):
    """Revokes all refresh tokens for the user across all devices."""
    revoke_all_refresh_tokens(user.id)
    clear_refresh_cookie(response)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return _to_user_out(user)


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------

@router.get("/google/login")
def google_login():
    """Step 1-2: Redirect user to Google."""
    if not settings.google_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Google login is not configured — set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET",
        )

    # CSRF protection: Store a random state in Redis to verify the callback.
    state = random_state()
    redis_client.setex(f"oauth:state:{state}", STATE_TTL_SECONDS, "1")

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "select_account",
    }
    url = httpx.URL(GOOGLE_AUTH_URL, params=params)
    return RedirectResponse(str(url))


@router.get("/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Step 4-6: Exchange code for user info and authenticate.
    """
    frontend = settings.FRONTEND_URL.rstrip("/")

    def fail(reason: str):
        return RedirectResponse(f"{frontend}/?auth_error={reason}")

    if error:
        return fail(error)
    if not code or not state:
        return fail("missing_code")

    # Verify and consume the state token.
    if not redis_client.delete(f"oauth:state:{state}"):
        return fail("invalid_state")

    try:
        with httpx.Client(timeout=10) as client:
            # ⭐ Step 5: Exchange code for token. Server-to-server call.
            token_res = client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )
            token_res.raise_for_status()
            access_token = token_res.json()["access_token"]

            info_res = client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            info_res.raise_for_status()
            info = info_res.json()

    except httpx.HTTPError as exc:
        logger.warning("Google OAuth fail: %s", exc)
        return fail("google_error")

    google_id = info.get("sub")
    email = (info.get("email") or "").lower()
    if not google_id or not email:
        return fail("no_email")

    # ---- Find or create user ----
    # Use google_id as the primary identifier.
    user = db.scalar(select(User).where(User.google_id == google_id))

    if user is None:
        # Link to existing email account if present.
        user = db.scalar(select(User).where(User.email == email))

        if user is None:
            user = User(
                email=email,
                hashed_password=None,
                full_name=info.get("name"),
                google_id=google_id,
                avatar_url=info.get("picture"),
            )
            db.add(user)
        else:
            user.google_id = google_id
            if not user.avatar_url:
                user.avatar_url = info.get("picture")

        db.commit()
        db.refresh(user)

    if not user.is_active:
        return fail("account_disabled")

    # Redirect to frontend after setting the refresh cookie.
    response = RedirectResponse(f"{frontend}/?auth=google")
    set_refresh_cookie(response, create_refresh_token(user.id))
    return response

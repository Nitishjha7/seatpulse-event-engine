"""
Auth routes — signup, login, refresh, logout, Google OAuth.

Google OAuth flow (Authorization Code) — kaun kis se baat karta hai:

  1. User "Continue with Google" dabata hai
     -> browser backend ke /google/login pe jata hai

  2. Backend user ko Google pe bhej deta hai (ek random `state` ke saath)

  3. User Google pe login karta hai aur permission deta hai

  4. Google user ko wapas /google/callback pe bhejta hai, ek `code` ke saath

  5. ⭐ BACKEND wo code Google ko wapas bhejta hai (client_secret ke saath)
     aur badle me user ki info leta hai. Ye step SERVER-TO-SERVER hai —
     browser is beech me hai hi nahi.

  6. Backend apna refresh cookie set karta hai aur user ko frontend pe
     redirect kar deta hai

Ye "Authorization Code" flow hai. Purana "Implicit" flow token seedha URL me
deta tha — wo browser history aur server logs me chhap jata tha, isliye ab
use nahi hota.

client_secret sirf backend ke paas rehta hai. Frontend-only OAuth me wo
secret browser me chala jata, jahan koi bhi use padh sakta hai.
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

# OAuth state 10 min me expire — itna time login ke liye kaafi hai
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
    """Access token JSON me, refresh token httpOnly cookie me."""
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
    """Frontend poochta hai: Google button dikhana hai ya nahi?"""
    return AuthConfigOut(google_enabled=settings.google_enabled)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Naya account. Signup ke baad seedha logged in — dobara login nahi karna padta."""
    # Ek IP se account farm banane se rokta hai.
    # Yahan IP hi use karna padta hai — abhi koi identity hai hi nahi.
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
        # Email pe unique constraint hai. Pehle SELECT karke check karte to
        # do parallel signups ke beech race reh jati — DB ko decide karne dena
        # hi sahi hai (wahi pattern jo seat booking me use kiya).
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Ye email pehle se registered hai")

    db.refresh(user)
    return _issue_tokens(response, user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    email = payload.email.lower()

    # ---- Brute force protection ----
    # ⭐ Limit EMAIL par hai, IP par nahi. Do wajah:
    #
    #   1. IP par lagate to office/college ke saare log ek doosre ki wajah
    #      se block ho jaate (sab ek hi NAT IP share karte hain)
    #   2. Attacker IP badal sakta hai, par jis account ko todna hai uska
    #      email nahi badal sakta — isliye email par limit zyada targeted hai
    #
    # Aur ye budget sirf GALAT password par kharch hota hai (neeche dekho).
    # Sahi login kabhi rate limit me nahi phasta.
    bucket = f"login:{email}"
    allowed, _, retry_after = check(bucket, LOGIN_FAIL, cost=0)   # cost=0 = sirf jhaank rahe hain
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Bahut zyada galat koshishein — thodi der baad try karo",
            headers={"Retry-After": str(retry_after)},
        )

    user = db.scalar(select(User).where(User.email == email))

    # ⚠️ Read ke baad transaction turant band kar do.
    #
    # Kyu: SQLAlchemy pehli query pe transaction khol deta hai aur wo
    # commit/close tak khuli rehti hai. Neeche bcrypt chalta hai jo
    # jaan-boojh ke ~100ms leta hai — utni der Postgres us connection ko
    # "idle in transaction" me pakde baitha rehta.
    #
    # Load test me exactly yahi dikha tha: 50 me se 50 connections
    # "idle in transaction", sirf 1 active. Pool khatam, users ko 500.
    db.commit()

    # ⚠️ "email nahi mila" aur "password galat" ke liye EK HI message.
    # Alag message dete to koi bhi email daal ke pata kar leta ki kaun
    # registered hai (user enumeration).
    if user is None or not verify_password(payload.password, user.hashed_password):
        # Sirf FAIL hone par token kharch hota hai.
        # Isliye ek genuine user jo roz login karta hai wo kabhi limit me
        # nahi phasta — sirf galat guesses count hote hain.
        check(bucket, LOGIN_FAIL, cost=1)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email ya password galat hai")

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled hai")

    return _issue_tokens(response, user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    """
    Cookie se naya access token lo.

    Frontend ise do jagah call karta hai:
      - page load pe (RAM me token nahi hota, isliye)
      - access token expire hone se thoda pehle (silent refresh)

    ⭐ ROTATION: purana refresh token turant revoke, naya issue.
    Isse token chori ka nuksaan kam hota hai — attacker use karega to
    asli user ka token invalid ho jayega aur uska logout ho jayega,
    jisse chori pakdi jati hai.
    """
    token = read_refresh_cookie(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token nahi mila")

    payload = decode_token(token, "refresh")
    user_id, jti = int(payload["sub"]), payload["jti"]

    # Redis whitelist check — logout ke baad token bekaar ho jata hai
    # bhale hi uski JWT expiry abhi baaki ho.
    if not refresh_token_is_valid(user_id, jti):
        clear_refresh_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token revoke ho chuka hai")

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        clear_refresh_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User nahi mila")

    revoke_refresh_token(user_id, jti)      # rotation
    return _issue_tokens(response, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response):
    """
    Logout — refresh token Redis se hata do aur cookie clear kar do.

    Access token 30 min tak technically valid rahega (JWT stateless hai).
    Isiliye use short rakha hai. Har request pe DB check karte to wo
    stateless ka faayda hi khatam ho jata.
    """
    token = read_refresh_cookie(request)
    if token:
        try:
            payload = decode_token(token, "refresh")
            revoke_refresh_token(int(payload["sub"]), payload["jti"])
        except HTTPException:
            pass    # token pehle se invalid tha — logout to phir bhi karna hai

    clear_refresh_cookie(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(response: Response, user: User = Depends(get_current_user)):
    """Sab devices se logout."""
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
    """Step 1-2: user ko Google pe bhejo."""
    if not settings.google_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Google login configure nahi hai — GOOGLE_CLIENT_ID/.SECRET set karo",
        )

    # CSRF protection: random string banao, Redis me rakho, Google ko bhejo.
    # Google wahi string wapas bhejega. Match na kare to matlab request
    # humne shuru nahi ki thi — reject.
    state = random_state()
    redis_client.setex(f"oauth:state:{state}", STATE_TTL_SECONDS, "1")

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        # Google ko batao ki refresh token chahiye (abhi use nahi kar rahe,
        # par aage Calendar waqerah integrate karna ho to kaam aayega)
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
    Step 4-6: Google se code lo, user info lo, login karao.

    Ye endpoint browser me khulta hai (Google redirect karta hai), isliye
    JSON nahi — frontend pe redirect karte hain.
    """
    frontend = settings.FRONTEND_URL.rstrip("/")

    def fail(reason: str):
        # Frontend URL me reason bhejte hain taki user ko kuch to pata chale
        return RedirectResponse(f"{frontend}/?auth_error={reason}")

    if error:
        # User ne "Cancel" dabaya — ye normal hai, error nahi
        return fail(error)
    if not code or not state:
        return fail("missing_code")

    # State verify — aur turant delete (ek baar hi use ho sakti hai)
    if not redis_client.delete(f"oauth:state:{state}"):
        return fail("invalid_state")

    try:
        with httpx.Client(timeout=10) as client:
            # ⭐ Step 5: code ko token se badlo. Server-to-server call —
            # client_secret yahan use hota hai aur browser tak kabhi nahi jata.
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

    # ---- User dhoondho ya banao ----
    # Pehle google_id se — kyunki user Google me apna email badal sakta hai,
    # par sub kabhi nahi badalta.
    user = db.scalar(select(User).where(User.google_id == google_id))

    if user is None:
        # Us email se password wala account pehle se hai? To use link kar do,
        # naya duplicate account mat banao.
        user = db.scalar(select(User).where(User.email == email))

        if user is None:
            user = User(
                email=email,
                hashed_password=None,        # Google user, koi password nahi
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

    # Refresh cookie set karke frontend pe bhej do.
    # Access token URL me NAHI bhej rahe — wo browser history aur server
    # logs me chhap jata. Frontend load hote hi /refresh call karke le lega.
    response = RedirectResponse(f"{frontend}/?auth=google")
    set_refresh_cookie(response, create_refresh_token(user.id))
    return response

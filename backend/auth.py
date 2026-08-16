"""
Authentication ka core — password hashing, JWT tokens, current-user dependency.

Token strategy (ye design decision hai, interview me poocha jata hai):

  ACCESS TOKEN   30 min   -> JSON me wapas jata hai, frontend RAM me rakhta hai
  REFRESH TOKEN  7 din    -> httpOnly cookie me, JavaScript use chhoo hi nahi sakta

Kyu aisa:
  - localStorage me token rakho to koi bhi XSS use padh sakta hai (koi bhi
    npm package, koi bhi injected script). httpOnly cookie JS se readable
    hi nahi hoti.
  - Par har request cookie se karna CSRF ka darwaza kholta hai. Isliye
    ASLI kaam access token karta hai (Authorization header se — jo CSRF
    me automatically nahi jata), aur cookie sirf naya access token lene
    ke liye use hoti hai.
  - Access token short hai, isliye chori ho bhi jaye to 30 min me mar jata hai.

Refresh token revocation:
  Har refresh token me ek `jti` (unique id) hota hai jo Redis me whitelist
  hota hai. Logout pe wo id Redis se hat jati hai -> token turant bekaar.
  Sirf JWT expiry par depend karte to logout ke baad bhi token 7 din chalta.
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

# auto_error=False -> token na ho to FastAPI khud 403 na de, hum apna
# saaf 401 message dena chahte hain.
_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """
    bcrypt — jaan-boojh ke DHEEMA algorithm.

    SHA256 jaisa fast hash yahan galat hai: attacker ek second me crores
    guesses kar leta. bcrypt har hash pe ~100ms leta hai, jisse brute force
    practically namumkin ho jata hai. Salt bhi apne aap andar aa jata hai.
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        # Google se bana user — iska koi password hai hi nahi
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
            "type": token_type,   # access token ko refresh ki tarah use na kiya ja sake
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
    Refresh token banao aur uski id Redis me whitelist karo.

    Redis key ki TTL token ki expiry ke barabar hai — purani entries
    apne aap saaf ho jaati hain, koi cleanup job nahi chahiye.
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
    Token verify karo. Kuch bhi galat ho to 401.

    jwt.decode() signature aur expiry dono khud check karta hai —
    hume manually kuch compare nahi karna.
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
    """Sab devices se logout. Password badalne par ye chalana chahiye."""
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
    Har protected route isse user nikalta hai.

    ⭐ Ab `user_id` request body se NAHI aata — token se aata hai.
    Pehle koi bhi {"user_id": 7} bhej ke kisi aur ke naam booking kar
    sakta tha. Ab token hi batata hai ki tum kaun ho.
    """
    if creds is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Login karna zaroori hai",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(creds.credentials, "access")
    user = db.get(User, int(payload["sub"]))

    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User nahi mila ya inactive hai")

    return user


def require_role(*roles: str):
    """
    Sirf in roles wale users ko andar aane do.

    Use:
        @router.post("", dependencies=[Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN))])
        # ya user object chahiye to:
        user: User = Depends(require_role(ROLE_ORGANIZER))

    ⚠️ 403 dete hain, 404 nahi.
    Booking wale IDOR case me 404 dete hain — wahan chhupana hai ki wo
    booking exist karti hai. Yahan chhupane ko kuch hai hi nahi: endpoint
    public knowledge hai (`/docs` me dikh raha hai), bas is user ke paas
    permission nahi. 403 hi sahi jawab hai.
    """

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Is kaam ke liye {' ya '.join(roles)} role chahiye",
            )
        return user

    return dependency


def get_current_user_optional(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User | None:
    """Login ho to user do, na ho to None — error mat do."""
    if creds is None:
        return None
    try:
        payload = decode_token(creds.credentials, "access")
        return db.get(User, int(payload["sub"]))
    except HTTPException:
        return None


def user_from_ws_token(token: str | None, db: Session) -> User | None:
    """
    WebSocket ke liye — token query param se aata hai.

    WebSocket handshake me custom headers nahi bhej sakte (browser API me
    wo option hi nahi hai), isliye `?token=...` use karte hain.

    Trade-off: URL server logs me aa sakta hai. Isliye yahan sirf SHORT-LIVED
    access token bhejte hain, refresh token kabhi nahi.
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
        httponly=True,      # JavaScript padh hi nahi sakta -> XSS se safe
        secure=settings.COOKIE_SECURE,   # dev me http hai isliye False
        samesite="lax",     # cross-site POST me cookie nahi jayegi -> CSRF se safe
        max_age=settings.REFRESH_TOKEN_DAYS * 24 * 3600,
        path="/api/auth",   # sirf auth routes pe jayegi, har request pe nahi
    )


def clear_refresh_cookie(response) -> None:
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/auth")


def read_refresh_cookie(request: Request) -> str | None:
    return request.cookies.get(REFRESH_COOKIE_NAME)


def random_state() -> str:
    """OAuth CSRF protection ke liye random string."""
    return secrets.token_urlsafe(24)

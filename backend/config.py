"""
App ki saari settings ek jagah.

Kyu: hardcoded values code me nahi honi chahiye. Aage Phase 2 me database URL
aur Phase 4 me Redis URL bhi yahin aayenge — tab tak ye pattern set ho jayega.

pydantic-settings apne aap environment variables padhta hai (aur .env file bhi),
aur types validate karta hai. Galat value di to app start hote hi error dega,
baad me kahin random jagah crash nahi hoga.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # .env file se padho. Container me env variables bhi kaam karenge —
    # environment variable ki priority .env file se zyada hoti hai.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "SeatPulse API"
    DEBUG: bool = True

    # docker-compose isko environment variable ke through bhejta hai.
    # "db" host ka naam compose service se aata hai — localhost yahan kaam nahi karega.
    DATABASE_URL: str = "postgresql+psycopg2://seatpulse:seatpulse_dev_password@db:5432/seatpulse"

    # SQL queries terminal me print karni hain? Debugging me kaam aata hai,
    # par logs bahut bhar jaate hain — default off.
    DB_ECHO: bool = False

    # Ek waqt me kitni requests andar aane dena hai (admission control).
    #
    # ⚠️ Ye DB pool se CHHOTA hona chahiye. Har chalti hui request ek DB
    # connection pakadti hai aur request khatam hone tak pakde rehti hai —
    # to agar in-flight requests pool se zyada ho gayin, to pool khatam
    # aur users ko 500.
    #
    # Invariant:  MAX_CONCURRENT_REQUESTS  <  pool_size + max_overflow
    #             (30 < 40)
    MAX_CONCURRENT_REQUESTS: int = 30

    # ---- Connection pool (Phase 16) ----
    #
    # Ye env se aane chahiye, hardcoded nahi — kyunki sahi value WORKERS
    # par nirbhar karti hai.
    #
    # Har uvicorn worker ek alag process hai aur uska APNA pool hota hai.
    # Yaani asli connections = WORKERS x (pool_size + max_overflow).
    # 4 workers x 40 = 160, aur Postgres ka default max_connections 100 hai.
    #
    # Single worker (dev): 20 + 20 = 40
    # 4 workers (prod):     5 +  5 = 40 total
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 20

    # ---- Benchmark mode (Phase 15) ----
    #
    # On hone par booking endpoint do extra query params maanta hai:
    # `strategy` (optimistic/pessimistic) aur `redis_lock` (on/off).
    #
    # Default OFF, aur ye jaan-boojh ke hai. Ek query param jo locking
    # semantics badal de, wo production me footgun hai — koi client
    # galti se (ya jaan-boojh ke) `?redis_lock=off` bhej ke sabse mehngi
    # code path chala sakta hai. Benchmark ke waqt env se on karte hain,
    # baaki hamesha optimistic + Redis.
    BENCHMARK_MODE: bool = False

    # "redis" host bhi compose service ka naam hai, "db" ki tarah.
    REDIS_URL: str = "redis://redis:6379/0"

    # ---------- Auth ----------
    # ⚠️ Production me ye MUST badalna hai. Isi se tokens sign hote hain —
    # leak ho gaya to koi bhi kisi ka bhi token bana sakta hai.
    # Naya banao: python -c "import secrets; print(secrets.token_urlsafe(48))"
    JWT_SECRET: str = "dev-only-secret-CHANGE-IN-PRODUCTION"
    JWT_ALGORITHM: str = "HS256"

    # Access token chhota rakhte hain — chori ho bhi jaye to 30 min me bekaar.
    ACCESS_TOKEN_MINUTES: int = 30
    # Refresh token lamba — user ko roz login na karna pade.
    REFRESH_TOKEN_DAYS: int = 7

    # Cookie sirf HTTPS par bheji jaye? Dev me http hai isliye False.
    # Production me hamesha True.
    COOKIE_SECURE: bool = False

    # ---------- Google OAuth ----------
    # Khali chhod do to Google login apne aap band rehta hai (frontend me
    # button hi nahi dikhega). Email/password phir bhi chalta rahega.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    # Ye Google Console me EXACTLY yahi register honi chahiye
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/auth/google/callback"

    # Google login ke baad user ko kahan wapas bhejna hai
    FRONTEND_URL: str = "http://localhost:5173"

    @property
    def google_enabled(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)

    # Rate limiting on/off.
    #
    # Load testing ke waqt kaam aata hai — limits per-user hain, to normal
    # load test waise bhi pass ho jata hai, par single-user stress test
    # karna ho to isse band kar sakte ho.
    RATE_LIMIT_ENABLED: bool = True

    # ---------- Payments ----------
    # Khali chhodo to MOCK provider chalta hai — poora flow bina Stripe
    # account ke test ho jata hai. Yahi pattern Google OAuth me use kiya tha.
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # User ke paas checkout complete karne ke liye kitna time hai.
    # Seat lock ki TTL bhi isi ke barabar kar dete hain — warna payment
    # ke beech me lock chhut jata aur koi aur seat le leta.
    PAYMENT_TTL_SECONDS: int = 600      # 10 minute

    CURRENCY: str = "INR"

    @property
    def payment_provider(self) -> str:
        """Keys hain to stripe, warna mock. Config me flag rakhne se behtar —
        ek hi jagah sach hai."""
        return "stripe" if self.STRIPE_SECRET_KEY else "mock"

    # Seat lock kitni der chalega (seconds).
    # 300 = 5 minute — itna time user ko payment ke liye milta hai.
    # Iske baad Redis khud key delete kar deta hai aur seat wapas available.
    #
    # Trade-off: chhota rakho to user checkout ke beech me seat kho de,
    # bada rakho to abandoned carts seats ghere rakhte hain.
    SEAT_LOCK_TTL: int = 300

    # Kaun se frontend origins API call kar sakte hain.
    # Comma se alag karke .env me likho: CORS_ORIGINS=http://localhost:5173,http://localhost:3000
    # Production me yahan asli domain aayega — ["*"] nahi.
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        """Comma-separated string ko list me todo, extra spaces hata ke."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


# Ek hi instance banao aur poore app me wahi use karo
settings = Settings()

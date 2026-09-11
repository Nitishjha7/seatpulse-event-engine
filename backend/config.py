"""
Centralized application configuration.

Rationale: Avoid hardcoded values. Database and Redis URLs will be added in
Phase 2 and Phase 4 respectively; this pattern ensures consistency.

pydantic-settings automatically reads environment variables (and .env files)
and validates types. Invalid values trigger an immediate startup error rather
than runtime crashes.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Read from .env file. Environment variables take precedence over .env.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "SeatPulse API"
    DEBUG: bool = True

    # Provided via environment variables in docker-compose.
    # "db" refers to the service name; localhost is not applicable here.
    DATABASE_URL: str = "postgresql+psycopg2://seatpulse:seatpulse_dev_password@db:5432/seatpulse"

    # Toggle SQL query logging. Useful for debugging, but verbose; default is off.
    DB_ECHO: bool = False

    # Admission control: maximum concurrent requests.
    #
    # ⚠️ Must be smaller than the DB pool size. Each request holds a DB
    # connection until completion; exceeding the pool size results in 500 errors.
    #
    # Invariant:  MAX_CONCURRENT_REQUESTS  <  pool_size + max_overflow
    #             (30 < 40)
    MAX_CONCURRENT_REQUESTS: int = 30

    # ---- Connection pool (Phase 16) ----
    #
    # Must be configured via environment variables as optimal values depend on
    # the number of WORKERS.
    #
    # Each uvicorn worker is a separate process with its own pool.
    # Total connections = WORKERS x (pool_size + max_overflow).
    #
    # Single worker (dev): 20 + 20 = 40
    # 4 workers (prod):     5 +  5 = 40 total
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 20

    # ---- Benchmark mode (Phase 15) ----
    #
    # Enables extra query params: `strategy` (optimistic/pessimistic) and
    # `redis_lock` (on/off).
    #
    # Default is OFF. Exposing locking semantics via query params is a security
    # risk in production, as clients could force inefficient code paths.
    BENCHMARK_MODE: bool = False

    # "redis" refers to the compose service name.
    REDIS_URL: str = "redis://redis:6379/0"

    # ---------- Auth ----------
    # ⚠️ Must be changed in production. Used for signing tokens; if leaked,
    # authentication is compromised.
    # Generate new: python -c "import secrets; print(secrets.token_urlsafe(48))"
    JWT_SECRET: str = "dev-only-secret-CHANGE-IN-PRODUCTION"
    JWT_ALGORITHM: str = "HS256"

    # Short-lived access tokens limit the impact of theft (30 min).
    ACCESS_TOKEN_MINUTES: int = 30
    # Long-lived refresh tokens improve user experience.
    REFRESH_TOKEN_DAYS: int = 7

    # Cookies must be secure (HTTPS) in production.
    COOKIE_SECURE: bool = False

    # ---------- Google OAuth ----------
    # If empty, Google login is disabled (frontend button hidden).
    # Email/password authentication remains active.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    # Must match the URI registered in Google Console exactly.
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/auth/google/callback"

    # Redirect target after successful Google login.
    FRONTEND_URL: str = "http://localhost:5173"

    @property
    def google_enabled(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)

    # Rate limiting toggle.
    #
    # Useful for load testing; disabling allows stress testing without
    # per-user rate limit interference.
    RATE_LIMIT_ENABLED: bool = True

    # ---------- Payments ----------
    # If empty, the MOCK provider is used, allowing full flow testing
    # without a Stripe account.
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # Time allowed for checkout completion.
    # Should match seat lock TTL to prevent lock expiration during payment.
    PAYMENT_TTL_SECONDS: int = 600      # 10 minute

    CURRENCY: str = "INR"

    # ---- Natural language seat search (Phase 19) ----
    #
    # Graceful degradation: if empty, the search UI is hidden.
    # ⚠️ Only disables natural language input; standard filters remain active.
    GEMINI_API_KEY: str = ""

    @property
    def ai_search_enabled(self) -> bool:
        return bool(self.GEMINI_API_KEY)

    @property
    def payment_provider(self) -> str:
        """Returns 'stripe' if keys are present, otherwise 'mock'."""
        return "stripe" if self.STRIPE_SECRET_KEY else "mock"

    # Seat lock duration (seconds).
    # 300 = 5 minutes. Redis automatically releases the lock after this period.
    #
    # Trade-off: Short TTLs risk premature lock expiration; long TTLs
    # increase the number of abandoned seats held in the system.
    SEAT_LOCK_TTL: int = 300

    # Allowed CORS origins.
    # Comma-separated list in .env: CORS_ORIGINS=http://localhost:5173,http://localhost:3000
    # Production must use specific domains, not ["*"].
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        """Parses comma-separated string into a list, stripping whitespace."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


# Singleton instance for application-wide use.
settings = Settings()

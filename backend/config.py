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

    # "redis" host bhi compose service ka naam hai, "db" ki tarah.
    REDIS_URL: str = "redis://redis:6379/0"

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

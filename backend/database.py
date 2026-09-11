"""
Database connection setup: engine, session, and Base.

Components:
  engine       -> The connection pool interface.
  SessionLocal -> Factory for request-scoped sessions.
  Base         -> Base class for all ORM models.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,

    # Validate connections before use to prevent "stale connection" errors
    # following database restarts.
    pool_pre_ping=True,

    # ⚠️ These values are tied to the thread pool size.
    #
    # Since routes are synchronous, FastAPI executes them in a thread pool
    # (default: 40 threads). Each request holds one connection from `get_db()`
    # for its entire duration.
    #
    # Requirement: pool_size + max_overflow > threadpool size.
    #
    # Previously, 10 + 20 = 30 caused "QueuePool limit reached" errors under
    # load, as bcrypt operations hold connections for ~100ms.
    #
    # Current thread pool is 32; pool 20 + 20 = 40 ensures sufficient capacity.
    # Postgres default max_connections is 100, keeping this safe.
    #
    # ⚠️ Phase 16: Values are now configurable. Note that in multi-worker
    # setups, each worker maintains its own pool (e.g., 4 workers x 40 = 160
    # connections), which may exceed Postgres limits. Production compose
    # settings are currently 5 + 5.
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    # Fail fast to identify pool exhaustion rather than waiting 30 seconds.
    pool_timeout=10,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,   # Manual commit for explicit transaction control.
    autoflush=False,    # Manual flush required for locking control in Phase 4.
)


class Base(DeclarativeBase):
    """Base class for all models; used by Alembic for table detection."""
    pass


def get_db():
    """
    FastAPI dependency providing a scoped DB session per request.

    Uses a generator to ensure the session closes after the request,
    preventing connection leaks and pool exhaustion.

    Usage: def route(db: Session = Depends(get_db))
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

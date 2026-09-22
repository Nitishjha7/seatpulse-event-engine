"""
Database connection setup: engine, session, and Base.

Components:
  engine       -> The connection pool interface.
  SessionLocal -> Factory for request-scoped sessions.
  Base         -> Base class for all ORM models.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,

    # Validate connections before use to prevent "stale connection" errors
    # following database restarts.
    pool_pre_ping=True,

    # Sized against the thread pool. Routes are synchronous, so FastAPI runs
    # them in a thread pool (default 32 threads), and each request holds one
    # connection from `get_db()` for its whole duration — so pool_size +
    # max_overflow needs to exceed the thread pool size or we hit
    # "QueuePool limit reached" under load (bcrypt alone holds a connection
    # for ~100ms). 20 + 20 = 40 covers it, well under Postgres's default
    # max_connections of 100.
    #
    # Configurable via env vars because multi-worker setups need smaller
    # per-worker pools — 4 workers x 40 would be 160 connections, over
    # Postgres's default limit. Prod compose sets these down to 5 + 5.
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    # Fail fast to identify pool exhaustion rather than waiting 30 seconds.
    pool_timeout=10,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,   # explicit transaction control
    autoflush=False,    # manual flush so seat-locking writes commit exactly where intended
)


class Base(DeclarativeBase):
    """Base class for all models; used by Alembic for table detection."""
    pass


def get_db():
    """
    FastAPI dependency providing a scoped DB session per request.

    Generator ensures the session closes after the request, so connections
    don't leak and exhaust the pool.

    Usage: def route(db: Session = Depends(get_db))
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

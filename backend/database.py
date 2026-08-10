"""
Database connection setup — engine, session, aur Base.

Teen cheezein yahan hain:
  engine       -> asli connection pool (DB se baat karne wala)
  SessionLocal -> har request ke liye ek naya session
  Base         -> saare models isi se inherit karte hain
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,

    # Connection use karne se pehle check karo ki wo abhi zinda hai.
    # Bina iske: DB restart hua to app "stale connection" errors dega.
    pool_pre_ping=True,

    # Kitne connections khule rakhne hain.
    # Phase 6 me 500 concurrent users aayenge — tab ye numbers matter karenge.
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,   # commit hum khud karenge, taki transaction control apne haath me rahe
    autoflush=False,    # flush bhi khud — Phase 4 me locking me ye control zaroori hai
)


class Base(DeclarativeBase):
    """Saare models isse inherit karenge. Alembic isi se tables detect karta hai."""
    pass


def get_db():
    """
    FastAPI dependency — har request ko apna DB session milta hai.

    Kyu generator: request khatam hone par session band ho jaye, chahe error
    hi kyu na aaye. Warna connections leak hote hain aur pool khatam ho jata hai.

    Use: def route(db: Session = Depends(get_db))
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

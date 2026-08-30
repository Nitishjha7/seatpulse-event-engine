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

    # ⚠️ Ye numbers thread pool se JUDE hue hain — random nahi hain.
    #
    # Hamare routes sync hain (`def`, `async def` nahi), isliye FastAPI unhe
    # ek threadpool me chalata hai (anyio ka default: 40 threads). Har chalti
    # hui request `get_db()` se EK connection pakadti hai aur poori request
    # tak pakde rehti hai.
    #
    # Matlab: pool_size + max_overflow  >  threadpool size
    #
    # Pehle 10 + 20 = 30 tha, jo 40 se kam hai. Load test me exactly wahi
    # phata:
    #     QueuePool limit of size 10 overflow 20 reached, connection timed out
    # Aur users ko 500 milne lage.
    #
    # Login me ye aur bura hota hai: bcrypt jaan-boojh ke ~100ms leta hai,
    # aur us poore time connection bandha rehta hai.
    #
    # Ab threadpool main.py me 32 pe fix hai, aur pool 20 + 20 = 40 > 32.
    # Postgres ka default max_connections 100 hai, to ye safe hai.
    #
    # ⚠️ Phase 16: ye ab config se aate hain, hardcoded nahi. Multi-worker
    # me har worker ka apna pool hota hai — 4 workers x 40 = 160 connections
    # maang lete, jo Postgres ki 100 wali limit todh deta. Prod compose me
    # ye 5 + 5 pe set hain.
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    # 30 sec chupchap wait karne se behtar hai jaldi fail hona — tab pata to
    # chale ki pool chhota pad raha hai.
    pool_timeout=10,
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

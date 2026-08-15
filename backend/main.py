from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import Event, Seat, User
from routers import bookings, events, seats
from schemas import UserOut

app = FastAPI(
    title=settings.APP_NAME,
    description="High-concurrency event ticketing engine",
    version="0.3.0",
)

# CORS: frontend 5173 pe hai, backend 8000 pe. Browser inhe alag websites
# maanta hai, isliye explicitly allow karna padta hai.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes ab alag files me hain. main.py sirf app banata aur jodta hai.
app.include_router(events.router)
app.include_router(seats.router)
app.include_router(bookings.router)


@app.get("/", tags=["meta"])
def read_root():
    return {"message": "FastAPI Server Running Perfectly!"}


@app.get("/api/health", tags=["meta"])
def health_check(db: Session = Depends(get_db)):
    """
    Health check. Database bhi verify karta hai.

    Sirf "app zinda hai" kaafi nahi — DB down ho to app kaam ka nahi.
    """
    try:
        db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as exc:
        db_status = f"error: {type(exc).__name__}"

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "service": settings.APP_NAME,
        "version": "0.3.0",
        "database": db_status,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/stats", tags=["meta"])
def stats(db: Session = Depends(get_db)):
    """Quick overview — seed data aur booking counts."""
    seats_by_status = db.execute(
        select(Seat.status, func.count(Seat.id)).group_by(Seat.status)
    ).all()

    return {
        "events": db.scalar(select(func.count(Event.id))),
        "seats_total": db.scalar(select(func.count(Seat.id))),
        "seats_by_status": {s: c for s, c in seats_by_status},
    }


@app.get("/api/me", response_model=UserOut, tags=["meta"])
def current_user(db: Session = Depends(get_db)):
    """
    Abhi ke liye pehla user return karta hai (demo user).

    ⚠️ Temporary hai. Phase 5 me JWT auth aayega aur ye asli logged-in user
    dega. Frontend ise use kar raha hai taki booking me user_id bhejna pade.
    """
    user = db.scalars(select(User).order_by(User.id).limit(1)).first()
    if user is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Koi user nahi mila — 'python seed.py' chalao"
        )
    return user

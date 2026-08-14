from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import Event, Seat

app = FastAPI(
    title=settings.APP_NAME,
    description="High-concurrency event ticketing engine",
    version="0.2.0",
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


@app.get("/")
def read_root():
    return {"message": "FastAPI Server Running Perfectly!"}


@app.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    """
    Health check. Ab database bhi check karta hai.

    Sirf "app zinda hai" kaafi nahi — agar DB down hai to app kaam ka nahi.
    Isliye ek sasti query (SELECT 1) maar ke connection verify karte hain.
    """
    try:
        db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as exc:
        db_status = f"error: {type(exc).__name__}"

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "service": settings.APP_NAME,
        "version": "0.2.0",
        "database": db_status,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/stats")
def stats(db: Session = Depends(get_db)):
    """
    Quick check ki seed data aaya ya nahi.

    Asli events/seats APIs Phase 3 me aayengi (Pydantic schemas ke saath).
    Ye sirf Phase 2 verify karne ke liye hai.
    """
    seats_by_status = db.execute(
        select(Seat.status, func.count(Seat.id)).group_by(Seat.status)
    ).all()

    return {
        "events": db.scalar(select(func.count(Event.id))),
        "seats_total": db.scalar(select(func.count(Seat.id))),
        "seats_by_status": {status: count for status, count in seats_by_status},
    }

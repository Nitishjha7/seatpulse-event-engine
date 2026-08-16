import asyncio
import anyio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from auth import user_from_ws_token
from config import settings
from database import SessionLocal, get_db
from models import Event, Seat
from redis_client import ping as redis_ping
from routers import admin, bookings, events, organizer, seats
from routers import auth as auth_router
from websocket import manager, start_subscriber


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    App start hone par Redis pub/sub subscriber chalu karo,
    band hone par saaf se rok do.

    Ye task poori app ki zindagi bhar chalta rehta hai — Redis se messages
    sunta hai aur is worker ke WebSocket clients ko forward karta hai.
    """
    # Threadpool admission limit se BADA rakha hai, taki jo request andar
    # aa chuki hai wo thread ka wait na kare (aur us dauraan DB connection
    # pakde na baithi rahe).
    anyio.to_thread.current_default_thread_limiter().total_tokens = 40

    task = start_subscriber()
    yield
    task.cancel()


app = FastAPI(
    title=settings.APP_NAME,
    description="High-concurrency event ticketing engine",
    version="0.6.0",
    lifespan=lifespan,
)

# CORS: frontend 5173 pe hai, backend 8000 pe. Browser inhe alag websites
# maanta hai, isliye explicitly allow karna padta hai.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    # ⚠️ Ab ye ZAROORI hai — refresh token cookie iske bina cross-origin
    # (5173 -> 8000) na bhejegi na set hogi. Aur credentials=True ke saath
    # allow_origins=["*"] browser reject kar deta hai, isliye specific
    # origins hi list me hain.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Admission control
# ---------------------------------------------------------------------------
# ⭐ Ye load test se aaya hua fix hai.
#
# Problem: hamare routes sync hain, aur `get_db` request ke SHURU me ek DB
# connection pakad leta hai — phir request threadpool slot ka wait karti
# hai, aur us poore intezaar me connection pakda hi rehta hai.
#
# Isliye "held connections" threadpool size se ZYADA ho jaate the. 200
# concurrent users pe pool khatam ho gaya aur users ko 500 milne lage:
#     QueuePool limit of size 20 overflow 20 reached, connection timed out
#
# Measure karke dekha tha: 40 me se 40 connections "idle in transaction",
# sirf 1 active. Matlab kaam koi nahi kar raha tha, sab connection pakde
# baithe the.
#
# Fix: darwaze pe hi rok lagao. Ek waqt me utni hi requests andar aane do
# jitni pool sambhal sake. Baaki queue me lagengi.
#
# Slow response >>> 500 error. Ye "admission control" kehlata hai aur
# har load-bearing service me hota hai.
_request_slots = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)


@app.middleware("http")
async def limit_concurrency(request, call_next):
    async with _request_slots:
        return await call_next(request)


# Routes ab alag files me hain. main.py sirf app banata aur jodta hai.
app.include_router(auth_router.router)
app.include_router(admin.router)
app.include_router(organizer.router)
app.include_router(events.router)
app.include_router(seats.router)
app.include_router(bookings.router)


@app.websocket("/ws/events/{event_id}")
async def event_socket(websocket: WebSocket, event_id: int, token: str | None = None):
    """
    Ek event ke live seat updates.

    Client connect karta hai, phir sirf sunta rehta hai. Jab bhi koi seat
    lock/unlock/book/cancel hoti hai, ye message aata hai:

        { "type": "seat_update", "action": "locked", "seat": { ...poora seat... } }

    ---- Auth ----
    Token QUERY PARAM se aata hai (`?token=...`), header se nahi — browser
    ka WebSocket API custom headers bhejne hi nahi deta.

    Trade-off: URL server logs me aa sakta hai. Isliye sirf short-lived
    ACCESS token bhejte hain (30 min), refresh token kabhi nahi.

    Token galat ho to 1008 (policy violation) ke saath band kar dete hain.
    Seat data khud public hai, par connection authenticate karna zaroori
    hai — warna koi bhi socket khol ke resources khaa sakta hai.
    """
    db = SessionLocal()
    try:
        user = user_from_ws_token(token, db)
    finally:
        db.close()

    if user is None:
        await websocket.close(code=1008, reason="Authentication required")
        return

    await manager.connect(websocket, event_id)
    try:
        while True:
            # Client se kuch expect nahi kar rahe. Ye receive isliye hai ki
            # connection zinda rahe aur disconnect ka pata chale.
            # Bina iske function turant return kar jata aur socket band ho jata.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket, event_id)


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

    redis_status = "connected" if redis_ping() else "unreachable"
    healthy = db_status == "connected" and redis_status == "connected"

    return {
        "status": "healthy" if healthy else "degraded",
        "service": settings.APP_NAME,
        "version": "0.6.0",
        "database": db_status,
        "redis": redis_status,
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


# NOTE: purana /api/me hata diya gaya — wo bina auth ke pehla user
# return karta tha. Ab GET /api/auth/me hai, jo token se user nikalta hai.

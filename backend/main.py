import asyncio
import os
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
from routers import group_bookings, admin, bookings, checkin, events, organizer, payments, search, seats
from routers import auth as auth_router
from websocket import manager, start_subscriber


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialize Redis pub/sub subscriber on startup and clean up on shutdown.

    This task runs for the duration of the application, listening for Redis
    messages and forwarding them to WebSocket clients.
    """
    # Set threadpool limit higher than the default to prevent requests from
    # waiting for threads while holding database connections.
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

# CORS: Frontend (5173) and backend (8000) are treated as distinct origins by browsers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    # ⚠️ Required for cross-origin cookie authentication (5173 -> 8000).
    # allow_credentials=True prevents the use of allow_origins=["*"].
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Admission control
# ---------------------------------------------------------------------------
# ⭐ Load test optimization.
#
# Problem: Synchronous routes acquire a DB connection via `get_db` before
# entering the threadpool. If the threadpool is saturated, connections remain
# idle in transaction, leading to pool exhaustion and 500 errors:
#     QueuePool limit of size 20 overflow 20 reached, connection timed out
#
# Observation: 40/40 connections were "idle in transaction" during peak load.
#
# Fix: Implement admission control to limit concurrent requests to the
# capacity of the connection pool.
#
# Slow response is preferable to 500 errors.
_request_slots = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)


@app.middleware("http")
async def limit_concurrency(request, call_next):
    async with _request_slots:
        return await call_next(request)


# Routes are modularized; main.py handles application assembly.
app.include_router(auth_router.router)
app.include_router(admin.router)
app.include_router(organizer.router)
app.include_router(events.router)
app.include_router(seats.router)
app.include_router(bookings.router)
app.include_router(payments.router)
app.include_router(checkin.router)
app.include_router(group_bookings.router)
app.include_router(search.router)


@app.websocket("/ws/events/{event_id}")
async def event_socket(websocket: WebSocket, event_id: int, token: str | None = None):
    """
    Live seat updates for a specific event.

    Clients receive messages in the format:
        { "type": "seat_update", "action": "locked", "seat": { ... } }

    ---- Auth ----
    Tokens are passed via query parameter (?token=...) because the browser
    WebSocket API does not support custom headers.

    Trade-off: Tokens may appear in server logs. Use short-lived access tokens
    (30 min) only.

    Invalid tokens result in a 1008 (policy violation) close code. Authentication
    is required to prevent unauthorized resource consumption.
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
            # Keep the connection alive and detect disconnections.
            # Without this, the function would return and close the socket.
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
    Health check including database and Redis connectivity.
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
        # Worker PID for debugging multi-worker deployments.
        #
        # Helps identify if specific workers are failing.
        # Phase 16: Validates load distribution across processes.
        "worker_pid": os.getpid(),
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/stats", tags=["meta"])
def stats(db: Session = Depends(get_db)):
    """Quick overview of seat counts by status."""
    seats_by_status = db.execute(
        select(Seat.status, func.count(Seat.id)).group_by(Seat.status)
    ).all()

    return {
        "events": db.scalar(select(func.count(Event.id))),
        "seats_total": db.scalar(select(func.count(Seat.id))),
        "seats_by_status": {s: c for s, c in seats_by_status},
    }


# NOTE: Removed legacy /api/me. Use GET /api/auth/me for token-based user retrieval.

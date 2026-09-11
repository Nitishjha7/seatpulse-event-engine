"""
Background worker — ARQ.

Usage:
    docker compose up worker          (service defined in compose)
    arq worker.WorkerSettings         (manual execution)

---- Why ARQ instead of Celery ----

Celery has a large ecosystem, but:
  - It requires a broker (RabbitMQ or Redis) — we already use Redis.
  - It is sync-first; our app is ASGI.
  - It is overly complex for our specific requirements.

ARQ runs directly on Redis (no new services), is asyncio-native, and is
compact (~1500 lines). It is a perfect fit for this project size.

We would only consider Celery if we needed: multiple queues with priorities,
complex workflows (chains/groups), or if the team required its ecosystem.
"""

import asyncio
import logging

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from config import settings
from database import SessionLocal
from groups import expire_due_groups
from models import (
    TICKET_FAILED,
    TICKET_READY,
    Booking,
    Event,
    Seat,
    User,
    utcnow,
)
from tickets import make_ticket_pdf, new_qr_token, save_ticket, send_ticket_email

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")


def _generate(booking_id: int) -> str:
    """
    Core logic — synchronous, as SQLAlchemy and reportlab are both sync.

    Runs in a separate thread (via `asyncio.to_thread`) to prevent blocking
    the event loop during PDF rendering, which would otherwise stall the worker.
    """
    db = SessionLocal()
    try:
        row = db.execute(
            select(Booking, Seat, Event, User)
            .join(Seat, Seat.id == Booking.seat_id)
            .join(Event, Event.id == Booking.event_id)
            .join(User, User.id == Booking.user_id)
            .where(Booking.id == booking_id)
        ).first()

        if row is None:
            raise ValueError(f"Booking {booking_id} not found")

        booking, seat, event, user = row

        # ⚠️ IDEMPOTENT: Jobs may run multiple times (ARQ retries or manual
        # re-enqueuing). Skip if already ready to avoid regenerating the
        # QR token and invalidating existing tickets.
        if booking.ticket_status == TICKET_READY and booking.qr_token:
            logger.info("Ticket for booking %s is already ready — skipping", booking_id)
            return booking.qr_token

        token = booking.qr_token or new_qr_token()

        pdf = make_ticket_pdf(
            token=token,
            booking_ref=f"SP{booking.id:05d}",
            event_name=event.name,
            venue=event.venue,
            starts_at=event.starts_at,
            seat_label=f"{seat.row_label}-{seat.seat_number}",
            amount=float(booking.amount),
            attendee=user.full_name or user.email.split("@")[0],
        )
        save_ticket(booking.id, pdf)

        send_ticket_email(
            to=user.email,
            subject=f"Your ticket for {event.name} — seat {seat.row_label}-{seat.seat_number}",
            body=(
                f"Hi {user.full_name or 'there'},\n\n"
                f"Your booking is confirmed.\n\n"
                f"  Event : {event.name}\n"
                f"  Venue : {event.venue}\n"
                f"  When  : {event.starts_at:%a, %d %b %Y at %I:%M %p}\n"
                f"  Seat  : {seat.row_label}-{seat.seat_number}\n"
                f"  Ref   : SP{booking.id:05d}\n\n"
                f"Your ticket is attached. Show the QR code at the gate.\n"
            ),
            pdf=pdf,
            booking_id=booking.id,
        )

        booking.qr_token = token
        booking.ticket_status = TICKET_READY
        booking.ticket_generated_at = utcnow()
        db.commit()

        logger.info("✅ Ticket ready: booking %s, seat %s-%s",
                    booking.id, seat.row_label, seat.seat_number)
        return token

    finally:
        db.close()


def _mark_failed(booking_id: int, reason: str) -> None:
    db = SessionLocal()
    try:
        booking = db.get(Booking, booking_id)
        if booking:
            booking.ticket_status = TICKET_FAILED
            db.commit()
        logger.error("❌ Ticket failed — booking %s: %s", booking_id, reason)
    finally:
        db.close()


async def generate_ticket(ctx: dict, booking_id: int) -> str:
    """
    ARQ job.

    ⚠️ Raising exceptions is intentional — ARQ handles retries based on
    `max_tries`. We only mark as `failed` after all retries are exhausted
    to prevent the user from seeing a permanent "generating..." state.
    """
    attempt = ctx.get("job_try", 1)
    logger.info("Generating ticket — booking %s (attempt %s)", booking_id, attempt)

    try:
        # PDF rendering is CPU-bound; offload to thread to keep event loop responsive.
        return await asyncio.to_thread(_generate, booking_id)
    except Exception as exc:
        if attempt >= WorkerSettings.max_tries:
            _mark_failed(booking_id, str(exc))
        raise    # Propagate to ARQ for retry


async def expire_groups(ctx) -> int:
    """
    Expire and release group bookings that have passed their deadline.

    ---- Why cron instead of lazy cleanup ----

    Other expired holds are cleaned up "lazily" when seats are accessed.
    This is efficient and sufficient for simple locks.

    Group bookings are different because they involve refunds. If no one
    visits the event page, lazy cleanup would never trigger, leaving users
    waiting indefinitely for their money.

    Refunds cannot depend on external user activity. Thus, this runs on a schedule.

    ⚠️ Job is idempotent: `break_group` uses an atomic conditional UPDATE,
    ensuring safety even if multiple workers run concurrently.
    """
    db = SessionLocal()
    try:
        broken = expire_due_groups(db)
        if broken:
            logger.info("Expired %s group booking(s)", broken)
        return broken
    finally:
        db.close()


class WorkerSettings:
    functions = [generate_ticket]

    # Run every 30 seconds. Group deadlines are in minutes, so 30s is
    # sufficient and avoids unnecessary database load.
    #
    # `run_at_startup` ensures groups that expired during downtime are
    # processed immediately upon worker restart.
    cron_jobs = [
        cron(expire_groups, second={0, 30}, run_at_startup=True),
    ]

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

    # 3 attempts with exponential backoff. Handles transient failures
    # (e.g., DB restarts, disk I/O spikes).
    max_tries = 3
    retry_delays = [5, 30]

    # 60s timeout for job execution.
    job_timeout = 60

    # Limit to 5 concurrent jobs; PDF rendering is CPU-bound, so higher
    # concurrency provides no benefit.
    max_jobs = 5

    # Keep results in Redis for 1 hour for debugging.
    keep_result = 3600

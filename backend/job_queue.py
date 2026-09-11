"""
Helper for job enqueuing.

Used by the API. Kept separate from worker code to prevent the API from
importing the worker, which avoids loading unnecessary dependencies like
reportlab and qrcode.
"""

import asyncio
import logging

from arq import create_pool
from arq.connections import RedisSettings

from config import settings

logger = logging.getLogger(__name__)


async def _enqueue(function: str, *args) -> str | None:
    redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        job = await redis.enqueue_job(function, *args)
        return job.job_id if job else None
    finally:
        await redis.close()


def enqueue_ticket(booking_id: int) -> None:
    """
    Add ticket generation to the queue.

    ⚠️ This never raises an exception.

    Rationale: Called after booking completion. Raising an error if Redis is
    down would return a 500 to the user despite a successful database
    transaction, which is unacceptable.

    On failure, the booking remains `ticket_status = pending` and is
    subsequently processed by `retry_pending_tickets.py`.

    This follows the same design as WebSocket broadcasts (Phase 5):
    notifications are "nice to have," while bookings are "must have."
    """
    try:
        # Executed from a synchronous context (FastAPI route); run in a
        # temporary event loop.
        asyncio.run(_enqueue("generate_ticket", booking_id))
        logger.info("Ticket job queued — booking %s", booking_id)
    except Exception as exc:
        logger.warning(
            "Ticket job failed to queue — booking %s: %s. "
            "retry_pending_tickets.py will handle this.",
            booking_id,
            exc,
        )

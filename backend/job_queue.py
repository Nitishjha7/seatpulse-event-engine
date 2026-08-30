"""
Job enqueue karne ka helper.

API side se use hota hai. Worker ke code se alag rakha hai taki API ko
worker import na karna pade (aur uske saath reportlab/qrcode bhi load na
hon — API ko unki zaroorat hi nahi).
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
    Ticket generation queue me daalo.

    ⚠️ Ye kabhi raise NAHI karta.

    Wajah: ye booking ho jaane ke BAAD call hota hai. Agar Redis down hai
    aur hum raise kar dein, to user ko 500 milega — jabki uska paisa kat
    chuka hai aur booking database me ban chuki hai. Wo sabse bura outcome
    hai.

    Fail hone par booking `ticket_status = pending` me rehti hai, aur
    `retry_pending_tickets.py` use baad me utha leta hai.

    Yahi soch WebSocket broadcast me bhi hai (Phase 5): notification
    "nice to have" hai, booking "must have".
    """
    try:
        # Ye sync context (FastAPI route) se call hota hai, isliye apna
        # chhota event loop chala ke turant band kar dete hain.
        asyncio.run(_enqueue("generate_ticket", booking_id))
        logger.info("Ticket job queued — booking %s", booking_id)
    except Exception as exc:
        logger.warning(
            "Ticket job queue nahi hua — booking %s: %s. "
            "retry_pending_tickets.py isse utha lega.",
            booking_id,
            exc,
        )

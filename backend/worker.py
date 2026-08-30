"""
Background worker — ARQ.

Chalao:
    docker compose up worker          (compose me service hai)
    arq worker.WorkerSettings         (manually)

---- ARQ kyu, Celery kyu nahi ----

Celery ka ecosystem bada hai, par:
  - usse ek broker chahiye (RabbitMQ ya Redis) — hamare paas Redis hai
  - wo sync-first hai; hamari app ASGI hai
  - config bahut zyada hai us kaam ke liye jo hume karna hai

ARQ Redis pe hi chalta hai (koi nayi service nahi), asyncio-native hai,
aur poora ~1500 lines ka hai. Is project ke size ke liye sahi fit.

Agli baar Celery tab chahiye hoga jab: multiple queues with priorities,
complex workflows (chains/groups), ya team ko uska ecosystem chahiye ho.
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
    Asli kaam — sync, kyunki SQLAlchemy aur reportlab dono sync hain.

    Ye ek THREAD me chalta hai (neeche `asyncio.to_thread`), warna PDF
    render karte waqt poora event loop block ho jata aur worker koi
    dusra job nahi utha pata.
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
            raise ValueError(f"Booking {booking_id} nahi mili")

        booking, seat, event, user = row

        # ⚠️ IDEMPOTENT: job do baar chal sakta hai (ARQ retry karta hai,
        # aur hum khud bhi re-enqueue kar sakte hain). Pehle se ready hai
        # to dobara mat banao — QR token badal jata to purana ticket
        # bekaar ho jata, jabki user uske paas already hai.
        if booking.ticket_status == TICKET_READY and booking.qr_token:
            logger.info("Booking %s ka ticket pehle se ready hai — skip", booking_id)
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
        logger.error("❌ Ticket fail — booking %s: %s", booking_id, reason)
    finally:
        db.close()


async def generate_ticket(ctx: dict, booking_id: int) -> str:
    """
    ARQ job.

    ⚠️ Yahan koi bhi exception raise karna THEEK hai — ARQ khud retry
    karta hai (`max_tries` neeche). Isliye hum error chupa nahi rahe.

    Par jab saari koshishein khatam ho jaayein, tab booking ko `failed`
    mark karte hain — warna user hamesha "generating..." dekhta rehta.
    """
    attempt = ctx.get("job_try", 1)
    logger.info("Ticket ban raha hai — booking %s (attempt %s)", booking_id, attempt)

    try:
        # PDF render CPU ka kaam hai — thread me bhejo, warna event loop
        # block hoga aur worker baaki jobs nahi utha payega
        return await asyncio.to_thread(_generate, booking_id)
    except Exception as exc:
        if attempt >= WorkerSettings.max_tries:
            _mark_failed(booking_id, str(exc))
        raise    # ARQ ko batao, wo retry karega


async def expire_groups(ctx) -> int:
    """
    Jinki deadline nikal gayi un group bookings ko todo.

    ---- Ye cron kyu hai, lazy cleanup kyu nahi ----

    Baaki jagah hum expired holds ko "lazy" saaf karte hain: jab koi seats
    padhta hai, tab purane locks release ho jaate hain. Wo sasta hai aur
    kaafi hai, kyunki wahan kuch khoya nahi jata — seat wapas available
    ho jati hai, bas.

    Group me aisa nahi hai. Group todne ka matlab **refund** bhi hai.
    Agar koi is event ka page hi na khole, to lazy cleanup kabhi chalta hi
    nahi — aur log apne paise ka intezaar karte reh jaate hain.

    Paisa wapas milna kisi ajnabi ke page kholne par nirbhar nahi ho sakta.
    Isliye ye ek schedule par chalta hai.

    ⚠️ Job idempotent hai: `break_group` ek atomic conditional UPDATE se
    chalta hai, to do worker ek saath chalein to bhi ek hi todega.
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

    # Har 30 second. Group deadline minutes me hoti hai, to 30 second ka
    # delay chalega — aur is se tez chalane ka matlab sirf khaali queries.
    #
    # `run_at_startup` isliye ki worker restart hone par jo groups us beech
    # expire ho gaye the wo turant nipat jaayein, agle tick ka wait na karein.
    cron_jobs = [
        cron(expire_groups, second={0, 30}, run_at_startup=True),
    ]

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

    # 3 koshishein, beech me badhta hua gap. Transient failure (DB restart,
    # disk busy) apne aap theek ho jaata hai.
    max_tries = 3
    retry_delays = [5, 30]

    # Ek job 60 second se zyada le to kuch to galat hai
    job_timeout = 60

    # Ek worker ek waqt me 5 tickets — PDF render CPU-bound hai, isse
    # zyada rakhne se kuch faayda nahi
    max_jobs = 5

    # Job results kitni der Redis me rahen (debugging ke liye)
    keep_result = 3600

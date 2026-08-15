"""Events ke routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import get_db
from models import SEAT_AVAILABLE, SEAT_BOOKED, SEAT_LOCKED, Event, Seat
from schemas import EventDetail, EventOut

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=list[EventOut])
def list_events(db: Session = Depends(get_db)):
    """Saare events, jo pehle shuru ho raha hai wo upar."""
    return db.scalars(select(Event).order_by(Event.starts_at)).all()


@router.get("/{event_id}", response_model=EventDetail)
def get_event(event_id: int, db: Session = Depends(get_db)):
    """Ek event + uski seats ka count status ke hisaab se."""
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event nahi mila")

    # Ek hi query me saare counts — har status ke liye alag query nahi maarni.
    counts = dict(
        db.execute(
            select(Seat.status, func.count(Seat.id))
            .where(Seat.event_id == event_id)
            .group_by(Seat.status)
        ).all()
    )

    return EventDetail(
        id=event.id,
        name=event.name,
        venue=event.venue,
        starts_at=event.starts_at,
        total_seats=event.total_seats,
        available_seats=counts.get(SEAT_AVAILABLE, 0),
        booked_seats=counts.get(SEAT_BOOKED, 0),
        locked_seats=counts.get(SEAT_LOCKED, 0),
    )

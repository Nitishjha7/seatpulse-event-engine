"""Seats ke routes. Locking Phase 4 me yahin add hogi."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import Event, Seat
from schemas import SeatOut

router = APIRouter(prefix="/api", tags=["seats"])


@router.get("/events/{event_id}/seats", response_model=list[SeatOut])
def list_event_seats(event_id: int, db: Session = Depends(get_db)):
    """
    Ek event ki saari seats — seat grid isi se banta hai.

    Row aur number se sort kar rahe hain taki frontend ko sort na karna pade.
    """
    if db.get(Event, event_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event nahi mila")

    return db.scalars(
        select(Seat)
        .where(Seat.event_id == event_id)
        .order_by(Seat.row_label, Seat.seat_number)
    ).all()


@router.get("/seats/{seat_id}", response_model=SeatOut)
def get_seat(seat_id: int, db: Session = Depends(get_db)):
    seat = db.get(Seat, seat_id)
    if seat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seat nahi mili")
    return seat

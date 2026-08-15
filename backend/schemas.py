"""
Pydantic schemas — API ka contract.

Models (models.py) = database ka shape
Schemas (ye file)  = API ka shape

Alag kyu rakhte hain:
  - `hashed_password` DB me hai par API me kabhi nahi jana chahiye
  - Client jo bhejta hai (BookingCreate) aur jo wapas milta hai (BookingOut) alag hain
  - FastAPI inhi se /docs banata hai aur incoming data validate karta hai
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# from_attributes=True -> SQLAlchemy object ko seedha schema me badal sakte hain.
# Iske bina har field haath se copy karni padti.
class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Event ----------

class EventOut(ORMModel):
    id: int
    name: str
    venue: str
    starts_at: datetime
    total_seats: int


class EventDetail(EventOut):
    """Event + seats ka summary. Grid load karne se pehle overview ke liye."""
    available_seats: int
    booked_seats: int
    locked_seats: int


# ---------- Seat ----------

class SeatOut(ORMModel):
    id: int
    event_id: int
    row_label: str
    seat_number: int
    price: float
    status: str
    # version client ko bhi bhej rahe hain — Phase 4 me booking request ke saath
    # wapas aayega, taki hum check kar sakein ki uske paas purana data to nahi tha.
    version: int


# ---------- Booking ----------

class BookingCreate(BaseModel):
    """Client jo bhejta hai."""
    seat_id: int = Field(..., gt=0, description="Kaunsi seat book karni hai")
    user_id: int = Field(..., gt=0, description="Kaun book kar raha hai (auth aane tak)")


class BookingOut(ORMModel):
    id: int
    user_id: int
    seat_id: int
    event_id: int
    status: str
    amount: float
    created_at: datetime


class BookingDetail(BookingOut):
    """Booking + seat ka pata, list dikhane ke liye."""
    seat_label: str
    event_name: str


# ---------- User ----------

class UserOut(ORMModel):
    """Note: hashed_password yahan NAHI hai — wo kabhi API se bahar nahi jana chahiye."""
    id: int
    email: str
    full_name: str | None

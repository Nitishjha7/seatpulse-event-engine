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

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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
    description: str | None = None
    category: str | None = None


class EventDetail(EventOut):
    """Event + seats ka summary. Grid load karne se pehle overview ke liye."""
    available_seats: int
    booked_seats: int
    locked_seats: int
    # Detail page pe "₹800 – ₹2500" dikhane ke liye. None jab koi seat na ho.
    min_price: float | None = None
    max_price: float | None = None


# ---------- Seat ----------

class SeatOut(ORMModel):
    id: int
    event_id: int
    row_label: str
    seat_number: int
    price: float
    status: str
    # version client ko bhi bhejte hain — isse UI me dikhta hai ki optimistic
    # locking actually kaam kar rahi hai (har change pe number badhta hai).
    version: int
    # Lock kiske paas hai. Frontend isse decide karta hai ki seat "meri hold"
    # (neeli) dikhani hai ya "kisi aur ki hold" (peeli).
    locked_by: int | None = None
    locked_until: datetime | None = None


# ---------- Seat Lock (Phase 4) ----------

class SeatLockOut(BaseModel):
    seat_id: int
    locked_by: int | None
    # Kitne second me lock apne aap chhut jayega. Frontend isse countdown chalata hai.
    expires_in: int
    # True = lock pehle se isi user ke paas tha (double-click waqerah)
    already_owned: bool = False
    # unlock call ke liye — False matlab lock TTL pe pehle hi expire ho chuka tha
    released: bool | None = None


# ---------- Booking ----------

class BookingCreate(BaseModel):
    """
    Client jo bhejta hai.

    ⭐ Note: `user_id` yahan NAHI hai. Pehle tha, aur wo ek security hole tha —
    koi bhi {"user_id": 7} bhej ke kisi aur ke naam booking kar sakta tha.
    Ab user JWT token se aata hai.
    """
    seat_id: int = Field(..., gt=0, description="Kaunsi seat book karni hai")


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
    avatar_url: str | None = None
    # Frontend isse decide karta hai ki "password badlo" option dikhana hai ya nahi
    is_google_user: bool = False


# ---------- Auth ----------

class RegisterRequest(BaseModel):
    # EmailStr galat format wala email pehle hi reject kar deta hai
    email: EmailStr
    # min_length=8 — Pydantic validation, route me check likhne ki zaroorat nahi
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str | None = Field(None, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """
    Sirf ACCESS token JSON me jata hai.

    Refresh token response body me kabhi nahi bhejte — wo httpOnly cookie
    me jata hai, jise JavaScript padh hi nahi sakti.
    """
    access_token: str
    token_type: str = "bearer"
    expires_in: int          # seconds — frontend isse silent refresh schedule karta hai
    user: UserOut


class AuthConfigOut(BaseModel):
    """Frontend poochta hai: Google login dikhana hai ya nahi?"""
    google_enabled: bool

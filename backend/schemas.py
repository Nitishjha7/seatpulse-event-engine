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

from typing import Literal

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


class GroupCreate(BaseModel):
    seat_ids: list[int] = Field(..., min_length=1, max_length=10)
    # None = default (30 min). Server apni limits khud lagata hai.
    deadline_minutes: int | None = Field(None, ge=5, le=120)


class GroupShareOut(BaseModel):
    id: int
    seat_id: int
    seat_label: str
    amount: float
    status: str
    claimed_by: int | None = None
    # Naam dikhate hain, email nahi — link kisi ke paas bhi ja sakta hai
    # aur usme sab members ke email dikhana privacy leak hai.
    claimed_by_name: str | None = None


class GroupOut(BaseModel):
    # ⚠️ `id` yahan JAAN-BOOJH ke nahi hai. Group hamesha share_token se
    # address hota hai. Sequential id bahar bhejne ka matlab hai ki koi
    # bhi 1, 2, 3 chala ke doosron ke groups dhoondh le.
    share_token: str
    event_id: int
    status: str
    expires_at: datetime
    seconds_left: int
    total_shares: int
    paid_shares: int
    shares: list[GroupShareOut]


class PricingOut(BaseModel):
    """Event ki abhi ki pricing state — UI ke surge badge ke liye."""
    enabled: bool
    multiplier: float
    surge_percent: int
    sold: int
    total: int
    # Agli price badhne se pehle kitni seats. None = pricing off ya max pe
    seats_until_increase: int | None = None


class EventDetail(EventOut):
    """Event + seats ka summary. Grid load karne se pehle overview ke liye."""
    available_seats: int
    booked_seats: int
    locked_seats: int
    # Detail page pe "₹800 – ₹2500" dikhane ke liye. None jab koi seat na ho.
    min_price: float | None = None
    max_price: float | None = None
    pricing: PricingOut | None = None


# ---------- Organizer (Phase 10) ----------

class PriceTier(BaseModel):
    """
    "Agli N rows is price par."

    Tiers upar se neeche lagte hain: pehla tier row A se shuru hota hai.
    Isse organizer VIP/normal/balcony ka pricing set kar sakta hai bina
    poora layout builder banaye (wo aage aayega).
    """
    rows: int = Field(..., gt=0, le=26, description="Kitni rows is tier me")
    price: float = Field(..., ge=0, le=1_000_000)


class EventCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=200)
    venue: str = Field(..., min_length=3, max_length=200)
    starts_at: datetime
    description: str | None = Field(None, max_length=5000)
    category: str | None = Field(None, max_length=40)

    seats_per_row: int = Field(..., gt=0, le=50)
    # Kam se kam ek tier. Total rows = sab tiers ka sum.
    price_tiers: list[PriceTier] = Field(..., min_length=1, max_length=10)

    # ---- Dynamic pricing (Phase 14) ----
    # Default OFF. Surge pricing har event ke liye theek nahi hai — free
    # community meetup pe ye ulta lagta hai. Organizer khud on kare.
    dynamic_pricing: bool = False
    # 0 = koi surge nahi, 1.0 = sab bikne par price double.
    # Upper bound 2.0 rakha hai — usse zyada kisi bhi normal event ke liye
    # bakwaas hai, aur galti se 50 type ho jaana bahut mehnga padta.
    demand_factor: float = Field(0.5, ge=0, le=2.0)
    # Hard ceiling. Chahe formula kuch bhi kahe, isse upar nahi jayega.
    max_surge: float = Field(2.0, ge=1.0, le=3.0)


class EventUpdate(BaseModel):
    """
    Sirf ye fields badal sakte hain.

    ⚠️ Seat layout ya pricing yahan nahi hai — jab log tickets khareed
    chuke hon, tab seats badalna ya price badalna galat hai. Uske liye
    event delete karke naya banana padega (aur delete tabhi hoga jab
    koi confirmed booking na ho).
    """
    name: str | None = Field(None, min_length=3, max_length=200)
    venue: str | None = Field(None, min_length=3, max_length=200)
    starts_at: datetime | None = None
    description: str | None = Field(None, max_length=5000)
    category: str | None = Field(None, max_length=40)

    # Pricing KNOBS badle ja sakte hain, base price nahi.
    #
    # Faraq ye hai: base price badalna purani bookings ko jhootha bana deta
    # ("₹800 ka ticket kaha tha, ab ₹1200 likha hai"). Surge band karna ya
    # halka karna sirf AAGE ki bookings pe asar daalta hai — jo har event
    # organizer ko karne ka haq hona chahiye agar sales slow ho rahi hain.
    dynamic_pricing: bool | None = None
    demand_factor: float | None = Field(None, ge=0, le=2.0)
    max_surge: float | None = Field(None, ge=1.0, le=3.0)


class OrganizerEventOut(EventOut):
    """Organizer ke apne event — sales ke saath."""
    available_seats: int
    locked_seats: int
    booked_seats: int
    revenue: float
    created_at: datetime


class AdminStatsOut(BaseModel):
    users: int
    organizers: int
    events: int
    seats: int
    bookings_confirmed: int
    bookings_cancelled: int
    revenue: float
    active_locks: int
    live_connections: int


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

    # ---- Pricing (Phase 14) ----
    # `price` BASE hai (kabhi nahi badalta). `current_price` abhi ka hai.
    # Dynamic pricing off ho to dono barabar rehte hain.
    current_price: float | None = None
    # Hold ke waqt lock hua price — checkout me yahi lagega
    held_price: float | None = None


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
    # Is hold ke liye LOCKED price. Checkout pe exactly yahi lagega —
    # frontend seedha yahi dikhata hai, dobara calculate nahi karta.
    price: float | None = None


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
    # pending | ready | failed — UI isse download button dikhata hai
    ticket_status: str = "pending"


# ---------- User ----------

class UserOut(ORMModel):
    """Note: hashed_password yahan NAHI hai — wo kabhi API se bahar nahi jana chahiye."""
    id: int
    email: str
    full_name: str | None
    avatar_url: str | None = None
    # attendee | organizer | admin — frontend isse nav gate karta hai
    role: str = "attendee"
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


# ---------- Payments (Phase 11) ----------

class CheckoutRequest(BaseModel):
    seat_id: int = Field(..., gt=0)


class CheckoutOut(BaseModel):
    payment_id: int
    # User ko yahan bhejo. Mock me hamara apna page, Stripe me unka.
    checkout_url: str
    provider: str          # "stripe" | "mock" — frontend UI adjust karta hai
    amount: float
    expires_at: datetime


class SimulateRequest(BaseModel):
    """Sirf mock provider ke liye — asli gateway me ye webhook se aata hai."""
    outcome: Literal["success", "fail"] = "success"


class PaymentOut(ORMModel):
    id: int
    seat_id: int
    event_id: int
    booking_id: int | None
    status: str
    amount: float
    currency: str
    provider: str
    failure_reason: str | None
    expires_at: datetime
    created_at: datetime


# ---------- Check-in (Phase 13) ----------

class CheckInRequest(BaseModel):
    token: str = Field(..., min_length=8, max_length=64)


class CheckInResult(BaseModel):
    """
    Gate ka jawab.

    ⚠️ `ok` field response body me hai, HTTP status me nahi. Gate pe khada
    banda status code nahi dekhta — use ek saaf jawab chahiye, aur uske
    saath wo jaankari jo dispute me kaam aaye (kab, kisne).
    """
    ok: bool
    # checked_in | already_checked_in | invalid_ticket | booking_cancelled | ticket_not_issued
    reason: str

    booking_id: int | None = None
    booking_ref: str | None = None
    seat_label: str | None = None
    event_name: str | None = None
    attendee_name: str | None = None

    checked_in_at: datetime | None = None
    already_checked_in: bool = False
    # Kisne scan kiya — duplicate ke case me "pehle kisne kiya tha"
    scanned_by: str | None = None

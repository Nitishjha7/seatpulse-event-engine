"""
Pydantic schemas — API contract.

Models (models.py) = Database schema
Schemas (this file) = API schema

Separation rationale:
  - `hashed_password` exists in the DB but must never be exposed via API.
  - Client input (BookingCreate) and output (BookingOut) structures differ.
  - FastAPI uses these for /docs generation and incoming data validation.
"""

from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# from_attributes=True allows direct conversion from SQLAlchemy objects.
# This avoids manual field-by-field copying.
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
    # None = default (30 min). Server enforces its own limits.
    deadline_minutes: int | None = Field(None, ge=5, le=120)


class GroupShareOut(BaseModel):
    id: int
    seat_id: int
    seat_label: str
    amount: float
    status: str
    claimed_by: int | None = None
    # Display name only, not email — links are shareable; exposing emails
    # of all members would be a privacy leak.
    claimed_by_name: str | None = None


class GroupOut(BaseModel):
    # ⚠️ `id` is intentionally omitted. Groups are addressed via share_token.
    # Exposing sequential IDs would allow enumeration of other groups.
    share_token: str
    event_id: int
    status: str
    expires_at: datetime
    seconds_left: int
    total_shares: int
    paid_shares: int
    shares: list[GroupShareOut]


class PricingOut(BaseModel):
    """Current event pricing state — for UI surge badges."""
    enabled: bool
    multiplier: float
    surge_percent: int
    sold: int
    total: int
    # Seats remaining before next price increase. None = pricing off or at max.
    seats_until_increase: int | None = None


class EventDetail(EventOut):
    """Event + seat summary for overview before loading the grid."""
    available_seats: int
    booked_seats: int
    locked_seats: int
    # For displaying price ranges like "₹800 – ₹2500". None if no seats exist.
    min_price: float | None = None
    max_price: float | None = None
    pricing: PricingOut | None = None
    # Used by the grid to render aisles and section headings.
    # None = legacy uniform event, render grid as before.
    layout: dict | None = None


# ---------- Organizer (Phase 10) ----------

class PriceTier(BaseModel):
    """
    Defines pricing for specific row ranges.

    Tiers are applied sequentially: the first tier starts at row A.
    Allows organizers to set VIP/normal/balcony pricing without a full
    layout builder.
    """
    rows: int = Field(..., gt=0, le=26, description="Number of rows in this tier")
    price: float = Field(..., ge=0, le=1_000_000)


class LayoutRow(BaseModel):
    label: str = Field(..., min_length=1, max_length=4)
    seats: int = Field(..., ge=1, le=60)
    # Indices after which to render a gap. Visual only — does not affect
    # seat count or numbering.
    aisles_after: list[int] = Field(default_factory=list, max_length=10)


class LayoutSection(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    price: float = Field(..., ge=0, le=1_000_000)
    rows: list[LayoutRow] = Field(..., min_length=1, max_length=40)


class SeatLayout(BaseModel):
    """
    Venue map.

    Pydantic validates shape (types, lengths). BUSINESS rules — duplicate
    row labels, seat caps, aisle positions — are handled in `layout.py`.

    This separation is intentional: shape rules are easy to define in
    schemas, but cross-field rules (e.g., unique row labels per section)
    require full layout context and should be testable without HTTP.
    """
    sections: list[LayoutSection] = Field(..., min_length=1, max_length=10)


class EventCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=200)
    venue: str = Field(..., min_length=3, max_length=200)
    starts_at: datetime
    description: str | None = Field(None, max_length=5000)
    category: str | None = Field(None, max_length=40)

    # ---- Seat generation strategy ----
    #
    # If `layout` is provided, it takes precedence. Otherwise, `price_tiers`
    # is used.
    #
    # Making both required is impractical: simple events shouldn't require
    # a full map, and layout-based events don't use `seats_per_row`.
    layout: SeatLayout | None = None

    seats_per_row: int = Field(10, gt=0, le=50)
    price_tiers: list[PriceTier] = Field(
        default_factory=lambda: [PriceTier(rows=5, price=500)],
        max_length=10,
    )

    # ---- Dynamic pricing (Phase 14) ----
    # Default OFF. Surge pricing is not suitable for all events (e.g., free
    # community meetups). Organizers must enable it explicitly.
    dynamic_pricing: bool = False
    # 0 = no surge, 1.0 = price doubles at capacity.
    # Capped at 2.0 to prevent extreme pricing errors.
    demand_factor: float = Field(0.5, ge=0, le=2.0)
    # Hard ceiling for surge pricing.
    max_surge: float = Field(2.0, ge=1.0, le=3.0)


class EventUpdate(BaseModel):
    """
    Mutable fields for existing events.

    ⚠️ Seat layout and pricing are excluded — modifying these after tickets
    are sold is prohibited. Events must be deleted and recreated (only
    possible if no confirmed bookings exist).
    """
    name: str | None = Field(None, min_length=3, max_length=200)
    venue: str | None = Field(None, min_length=3, max_length=200)
    starts_at: datetime | None = None
    description: str | None = Field(None, max_length=5000)
    category: str | None = Field(None, max_length=40)

    # Pricing knobs are adjustable, but base price is not.
    #
    # Changing base price invalidates existing bookings. Adjusting surge
    # settings only affects future bookings, which is acceptable for
    # managing slow sales.
    dynamic_pricing: bool | None = None
    demand_factor: float | None = Field(None, ge=0, le=2.0)
    max_surge: float | None = Field(None, ge=1.0, le=3.0)


class OrganizerEventOut(EventOut):
    """Organizer view with sales metrics."""
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
    # "Ground" / "Balcony" — for layout-based events. None for legacy.
    section: str | None = None
    price: float
    status: str
    # Version is sent to client to verify optimistic locking (increments
    # on every change).
    version: int
    # Lock owner. Frontend uses this to distinguish between "my hold"
    # (blue) and "someone else's hold" (yellow).
    locked_by: int | None = None
    locked_until: datetime | None = None

    # ---- Pricing (Phase 14) ----
    # `price` is the immutable base price. `current_price` is the dynamic price.
    current_price: float | None = None
    # Price locked at the time of hold — applied during checkout.
    held_price: float | None = None


# ---------- Seat Lock (Phase 4) ----------

class SeatLockOut(BaseModel):
    seat_id: int
    locked_by: int | None
    # Seconds until lock expires. Used for frontend countdown.
    expires_in: int
    # True = lock already held by this user (e.g., double-click).
    already_owned: bool = False
    # For unlock calls — False means the lock already expired via TTL.
    released: bool | None = None
    # Price locked for this hold. Applied at checkout.
    price: float | None = None


# ---------- Booking ----------

class BookingCreate(BaseModel):
    """
    Client input.

    ⭐ Note: `user_id` is excluded. Previously, this was a security hole
    allowing users to book on behalf of others. User identity is now
    derived from the JWT token.
    """
    seat_id: int = Field(..., gt=0, description="Seat to book")


class BookingOut(ORMModel):
    id: int
    user_id: int
    seat_id: int
    event_id: int
    status: str
    amount: float
    created_at: datetime


class BookingDetail(BookingOut):
    """Booking + seat details for list views."""
    seat_label: str
    event_name: str
    # pending | ready | failed — determines download button visibility.
    ticket_status: str = "pending"


# ---------- User ----------

class UserOut(ORMModel):
    """Note: hashed_password is excluded — never expose via API."""
    id: int
    email: str
    full_name: str | None
    avatar_url: str | None = None
    # attendee | organizer | admin — used for navigation gating.
    role: str = "attendee"
    # Used to determine if "change password" option should be shown.
    is_google_user: bool = False


# ---------- Auth ----------

class RegisterRequest(BaseModel):
    # EmailStr validates format automatically.
    email: EmailStr
    # min_length=8 — Pydantic validation replaces manual route checks.
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str | None = Field(None, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """
    Only the ACCESS token is returned in JSON.

    Refresh tokens are sent via httpOnly cookies to prevent JS access.
    """
    access_token: str
    token_type: str = "bearer"
    expires_in: int          # seconds — used for silent refresh scheduling.
    user: UserOut


class AuthConfigOut(BaseModel):
    """Frontend configuration for optional features."""
    google_enabled: bool
    # AI search box visibility. If missing, frontend does not render the box.
    ai_search_enabled: bool = False


# ---------- Seat search (Phase 19) ----------

class SeatFilters(BaseModel):
    """
    Search filters.

    ⚠️ This is the contract between the LLM and the search engine, serving
    as the security boundary.

    All LLM output is validated here: ranges are clamped, unknown fields
    are dropped, and types are enforced before reaching `seat_search.find()`.
    This prevents prompt injection from executing arbitrary SQL.
    """
    quantity: int = Field(1, ge=1, le=10)
    together: bool = True
    min_price: float | None = Field(None, ge=0, le=1_000_000)
    max_price: float | None = Field(None, ge=0, le=1_000_000)
    section: str | None = Field(None, max_length=40)
    row_preference: Literal["front", "middle", "back"] | None = None


class EventDraftRequest(BaseModel):
    """Organizer brief — e.g., "Arijit Singh, DY Patil Mumbai, December"."""
    brief: str = Field(..., min_length=5, max_length=200)


class EventDraftOut(BaseModel):
    """
    AI-generated draft.

    ⚠️ Never saved directly. Populates the organizer form for manual review
    and publication.

    Event descriptions are a commitment to ticket buyers; human oversight
    is mandatory.
    """
    name: str
    description: str
    category: str


class SeatSearchRequest(BaseModel):
    # Natural language query. Ignored if AI is disabled.
    query: str | None = Field(None, max_length=200)
    # Explicit filters. UI sends both: query generates filters, which
    # the user can then manually override.
    filters: SeatFilters | None = None


class SeatMatch(BaseModel):
    seat_ids: list[int]
    label: str
    row_label: str
    section: str | None = None
    seat_numbers: list[int]
    total_price: float


class SeatSearchOut(BaseModel):
    matches: list[SeatMatch]
    # The filters actually applied. Essential for transparency so the user
    # understands how their query was interpreted.
    filters: SeatFilters
    # Whether the AI successfully interpreted the query.
    interpreted: bool = False


# ---------- Payments (Phase 11) ----------

class CheckoutRequest(BaseModel):
    seat_id: int = Field(..., gt=0)


class CheckoutOut(BaseModel):
    payment_id: int
    # Redirect URL (mock page or Stripe gateway).
    checkout_url: str
    provider: str          # "stripe" | "mock" — adjusts UI behavior.
    amount: float
    expires_at: datetime


class SimulateRequest(BaseModel):
    """For mock provider only — real gateways use webhooks."""
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
    Gate response.

    ⚠️ `ok` is in the body, not the HTTP status. Gate operators need a
    clear response, along with audit data for dispute resolution.
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
    # Who scanned the ticket (for duplicate scan audits).
    scanned_by: str | None = None

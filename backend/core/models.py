"""
Database models.

The "no overselling" guarantee lives in these constraints, not in application
logic. Three layers, fastest at top, most robust at bottom:
  1. Redis lock          -> fast rejection, keeps load off the DB
  2. version column      -> optimistic locking, one of two parallel updates fails
  3. UNIQUE constraint   -> database-level enforcement, holds even if the code has a bug
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    func,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


def utcnow() -> datetime:
    """Always returns timezone-aware UTC to avoid comparison issues with naive datetimes."""
    return datetime.now(timezone.utc)


# Seat states
SEAT_AVAILABLE = "available"
SEAT_LOCKED = "locked"      # Selected by a user, pending payment
SEAT_BOOKED = "booked"
# Payment in progress — seat is held but not yet sold.
# Separate status allows the UI to show "purchase in progress" and enables
# specific cleanup logic distinct from standard locks.
SEAT_PAYMENT_PENDING = "payment_pending"
# Held by a group booking.
#
# Must be distinct from `locked`.
#
# `locked` and `payment_pending` seats are cleaned up by `release_expired_locks`
# via TTL. Group seats cannot be released this way because some users may have
# already paid. Releasing them requires a refund, which is handled by a
# background job, not a read request.
#
# Lazy cleanup ignores this status.
SEAT_GROUP_HELD = "group_held"

# Booking states
BOOKING_PENDING = "pending"
BOOKING_CONFIRMED = "confirmed"
BOOKING_CANCELLED = "cancelled"

# Payment states
PAYMENT_PENDING = "pending"       # Session created, user at gateway
PAYMENT_SUCCEEDED = "succeeded"   # Confirmed via webhook
PAYMENT_FAILED = "failed"         # Gateway rejection
PAYMENT_EXPIRED = "expired"       # Window closed, no response
PAYMENT_REFUNDED = "refunded"

# Ticket generation states
TICKET_PENDING = "pending"     # Queued or in progress
TICKET_READY = "ready"         # QR + PDF generated
TICKET_FAILED = "failed"       # Worker failure, eligible for retry

ALL_TICKET_STATUSES = (TICKET_PENDING, TICKET_READY, TICKET_FAILED)

ALL_PAYMENT_STATUSES = (
    PAYMENT_PENDING,
    PAYMENT_SUCCEEDED,
    PAYMENT_FAILED,
    PAYMENT_EXPIRED,
    PAYMENT_REFUNDED,
)

# User roles. Flat structure — granular permissions (e.g. event.create) are
# overkill at this scale, and easier to add later than to remove.
ROLE_ATTENDEE = "attendee"     # View and book seats
ROLE_ORGANIZER = "organizer"   # Create and manage events
ROLE_ADMIN = "admin"           # Full platform access

ALL_ROLES = (ROLE_ATTENDEE, ROLE_ORGANIZER, ROLE_ADMIN)

# ---- Group booking ----

GROUP_COLLECTING = "collecting"   # Link shared, payments in progress
GROUP_CONFIRMED = "confirmed"     # All payments received, seats secured
GROUP_EXPIRED = "expired"         # Deadline passed, seats released
GROUP_CANCELLED = "cancelled"     # Cancelled by the creator

ALL_GROUP_STATUSES = (GROUP_COLLECTING, GROUP_CONFIRMED, GROUP_EXPIRED, GROUP_CANCELLED)

SHARE_UNPAID = "unpaid"
SHARE_PAID = "paid"
SHARE_REFUNDED = "refunded"       # Group dissolved, payment refunded

ALL_SHARE_STATUSES = (SHARE_UNPAID, SHARE_PAID, SHARE_REFUNDED)

ALL_SEAT_STATUSES = (
    SEAT_AVAILABLE,
    SEAT_LOCKED,
    SEAT_PAYMENT_PENDING,
    SEAT_BOOKED,
    SEAT_GROUP_HELD,
)
SEAT_STATUS_SQL = ", ".join(repr(s) for s in ALL_SEAT_STATUSES)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    # Nullable for Google-authenticated users who do not have a local password.
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Google "sub" claim — permanent unique ID.
    # Email is not used as a primary key as it can change in Google accounts.
    google_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # attendee | organizer | admin
    role: Mapped[str] = mapped_column(
        String(16), default=ROLE_ATTENDEE, nullable=False, index=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Explicit foreign_keys required: Booking has two FKs to User
    # (user_id = purchaser, checked_in_by = staff who scanned the ticket),
    # so SQLAlchemy needs the explicit path to resolve the ambiguity.
    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="user", passive_deletes=True, foreign_keys="Booking.user_id"
    )

    __table_args__ = (
        # Enforce valid roles at the database level.
        CheckConstraint(
            f"role IN ({', '.join(repr(r) for r in ALL_ROLES)})",
            name="ck_user_role",
        ),
    )

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    venue: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_seats: Mapped[int] = mapped_column(Integer, default=0)

    # Text type used for descriptions to avoid length limits.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # UI tags (e.g., "Music", "Comedy").
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # ---- Seat layout ----
    # Venue map (sections, rows, aisles). Nullable for legacy events — NULL
    # means a simple uniform grid. Not the source of truth for seats (the
    # `seats` table is); this JSON is just for rendering the grid and aisles.
    layout: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ---- Dynamic pricing ----
    # Off by default to preserve legacy event behavior.
    dynamic_pricing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 0.5 = 1.5x price at 100% capacity.
    demand_factor: Mapped[float] = mapped_column(Numeric(4, 2), default=0.5, nullable=False)
    # Price cap to maintain user trust.
    max_surge: Mapped[float] = mapped_column(Numeric(4, 2), default=2.0, nullable=False)

    # Organizer association. Nullable for legacy/admin-created events.
    # ondelete="SET NULL" so events survive an organizer account deletion.
    organizer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # passive_deletes=True required — otherwise SQLAlchemy tries to load
    # children and null out their FKs itself, conflicting with the
    # database-level ON DELETE CASCADE.
    seats: Mapped[list["Seat"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Event {self.name}>"


class Seat(Base):
    """
    Core table for seat management.
    """

    __tablename__ = "seats"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )

    # Seat coordinates
    row_label: Mapped[str] = mapped_column(String(4))
    seat_number: Mapped[int] = mapped_column(Integer)

    # Section (e.g., "Ground", "Balcony"). Nullable for legacy compatibility;
    # kept here for ticket/check-in access.
    section: Mapped[str | None] = mapped_column(String(40), nullable=True)

    price: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

    # available | locked | payment_pending | booked
    status: Mapped[str] = mapped_column(String(24), default=SEAT_AVAILABLE, index=True)

    # ---- OPTIMISTIC LOCKING ----
    # Incremented on every successful update. Avoids row-level locking — a
    # version mismatch fails the update (rowcount 0) and returns 409.
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ---- Price lock ----
    # Freezes the price at hold time so it can't shift during checkout.
    # Cleared when the hold is released.
    held_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)

    # ---- Redis lock integration ----
    # Redis handles the primary lock. These columns provide a persistent record.
    locked_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    event: Mapped["Event"] = relationship(back_populates="seats")
    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="seat", passive_deletes=True
    )

    __table_args__ = (
        # Enforce unique seat position per event.
        UniqueConstraint("event_id", "row_label", "seat_number", name="uq_seat_position"),

        # Enforce valid status values.
        CheckConstraint(
            f"status IN ({SEAT_STATUS_SQL})",
            name="ck_seat_status",
        ),

        # Index for common seat grid queries.
        Index("ix_seat_event_status", "event_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<Seat {self.row_label}-{self.seat_number} {self.status}>"


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)

    status: Mapped[str] = mapped_column(String(16), default=BOOKING_CONFIRMED, index=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

    # ---- Ticket ----
    # Booking and Ticket are 1:1. qr_token is random and unique so it can't
    # be guessed sequentially.
    qr_token: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    ticket_status: Mapped[str] = mapped_column(
        String(16), default=TICKET_PENDING, nullable=False
    )
    ticket_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ---- Check-in ----
    # checked_in_at doubles as a guard — updates only succeed while it's NULL.
    checked_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checked_in_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="bookings", foreign_keys=[user_id])
    seat: Mapped["Seat"] = relationship(back_populates="bookings")

    __table_args__ = (
        CheckConstraint(
            f"status IN ('{BOOKING_PENDING}', '{BOOKING_CONFIRMED}', '{BOOKING_CANCELLED}')",
            name="ck_booking_status",
        ),

        # ---- FINAL OVERSALE PROTECTION ----
        # Partial unique index: only one confirmed booking per seat. Cancelled
        # bookings are excluded so the seat can be re-sold. This is the last
        # line of defense against race conditions.
        Index(
            "uq_one_confirmed_booking_per_seat",
            "seat_id",
            unique=True,
            postgresql_where=text(f"status = '{BOOKING_CONFIRMED}'"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Booking user={self.user_id} seat={self.seat_id} {self.status}>"


class Payment(Base):
    """
    Represents a checkout attempt.

    Kept separate from Booking so failed attempts have a history and retries
    can be handled correctly.
    """

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)

    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(16), default=PAYMENT_PENDING, nullable=False, index=True
    )
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    provider: Mapped[str] = mapped_column(String(20))

    # Unique provider reference ensures webhook idempotency.
    provider_ref: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )

    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ---- Group booking ----
    # If set, this payment belongs to a group share. The booking is only
    # created once all shares are fulfilled.
    group_share_id: Mapped[int | None] = mapped_column(
        ForeignKey("group_shares.id", ondelete="SET NULL"), nullable=True, index=True
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped["User"] = relationship()
    seat: Mapped["Seat"] = relationship()

    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in ALL_PAYMENT_STATUSES)})",
            name="ck_payment_status",
        ),
        # Partial unique index: one pending payment per seat, so a seat
        # can't have multiple checkout sessions at once.
        Index(
            "uq_one_pending_payment_per_seat",
            "seat_id",
            unique=True,
            postgresql_where=text(f"status = '{PAYMENT_PENDING}'"),
        ),
        Index("ix_payment_status_expires", "status", "expires_at"),
    )

    def __repr__(self) -> str:
        return f"<Payment {self.id} {self.status} {self.amount}>"


class GroupBooking(Base):
    """
    Represents a group booking (N seats, N payments).

    Seats are held until all payments are received. Bookings are created
    atomically once the group is confirmed.
    """

    __tablename__ = "group_bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    status: Mapped[str] = mapped_column(
        String(16), default=GROUP_COLLECTING, nullable=False, index=True
    )

    # Secret token for group access.
    share_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # Expiry is a business decision, so a background job manages it rather than Redis TTL.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    event: Mapped["Event"] = relationship()
    creator: Mapped["User"] = relationship()
    shares: Mapped[list["GroupShare"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(repr(x) for x in ALL_GROUP_STATUSES) + ")",
            name="ck_group_status",
        ),
    )


class GroupShare(Base):
    """
    Represents a single seat share within a group.
    """

    __tablename__ = "group_shares"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("group_bookings.id", ondelete="CASCADE"), index=True
    )
    seat_id: Mapped[int] = mapped_column(
        ForeignKey("seats.id", ondelete="CASCADE"), index=True
    )

    # Nullable: seat is unassigned until claimed.
    claimed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id", ondelete="SET NULL"), nullable=True
    )
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(16), default=SHARE_UNPAID, nullable=False, index=True
    )
    # Frozen here so surge pricing during the collection window can't affect it.
    amount: Mapped[float] = mapped_column(Numeric(10, 2))

    group: Mapped["GroupBooking"] = relationship(back_populates="shares")
    seat: Mapped["Seat"] = relationship()

    __table_args__ = (
        # Ensure one seat per group.
        UniqueConstraint("group_id", "seat_id", name="uq_group_seat"),
        CheckConstraint(
            "status IN (" + ", ".join(repr(x) for x in ALL_SHARE_STATUSES) + ")",
            name="ck_share_status",
        ),
    )

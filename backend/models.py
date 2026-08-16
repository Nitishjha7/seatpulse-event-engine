"""
Database models.

Ye file poore project ki neev hai. "Overselling nahi hoga" wala claim
aakhir me in constraints par tikta hai — application code par nahi.

Teen layer ki safety (sabse upar sabse tez, sabse neeche sabse pakka):
  1. Redis lock          -> Phase 4 me. Fast rejection, DB tak load hi nahi aata
  2. version column      -> optimistic locking. Do parallel update me ek fail hoga
  3. UNIQUE constraint   -> database ka apna niyam. Code me bug ho to bhi ye nahi tootega
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def utcnow() -> datetime:
    """Hamesha timezone-aware UTC. Naive datetime aage compare me dard deta hai."""
    return datetime.now(timezone.utc)


# Seat ki possible haalat
SEAT_AVAILABLE = "available"
SEAT_LOCKED = "locked"      # kisi ne select kiya hai, abhi pay nahi kiya (Phase 4)
SEAT_BOOKED = "booked"

# Booking ki possible haalat
BOOKING_PENDING = "pending"
BOOKING_CONFIRMED = "confirmed"
BOOKING_CANCELLED = "cancelled"

# User roles.
#
# Sirf teen hain aur jaan-boojh ke flat hain — koi permission matrix nahi.
# Ek chhote system me granular permissions (event.create, event.delete...)
# over-engineering hoti hai. Zaroorat padne par flat role se granular pe
# jaana aasan hai; ulta bahut mushkil.
ROLE_ATTENDEE = "attendee"     # seats dekho aur book karo
ROLE_ORGANIZER = "organizer"   # apne events banao aur manage karo
ROLE_ADMIN = "admin"           # poore platform ka access

ALL_ROLES = (ROLE_ATTENDEE, ROLE_ORGANIZER, ROLE_ADMIN)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    # nullable=True kyunki Google se aane wale users ka koi password hota hi nahi.
    # Unke liye ye NULL rehta hai aur login sirf Google se hota hai.
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Google ka "sub" claim — permanent unique id.
    # Email par match nahi karte kyunki user Google me email badal sakta hai.
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

    bookings: Mapped[list["Booking"]] = relationship(back_populates="user")

    __table_args__ = (
        # Typo se koi "Organizer" ya "orgnizer" na ban jaye — DB hi rok dega
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

    # Event detail page ke liye. Text (String nahi) kyunki description
    # lambi ho sakti hai aur uspe koi length limit lagane ka matlab nahi.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "Music", "Comedy", "Sports" — UI me tag ki tarah dikhta hai
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Kis organizer ka event hai.
    #
    # nullable=True do wajah se:
    #   1. Purane events (migration se pehle wale) ka koi owner nahi tha
    #   2. Admin bina organizer ke bhi event bana sakta hai
    #
    # ondelete="SET NULL" — organizer ka account delete ho to event aur
    # uski bookings nahi udni chahiye. Log ne paise diye hain.
    organizer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # cascade: event delete hua to uski seats bhi jaayengi
    seats: Mapped[list["Seat"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Event {self.name}>"


class Seat(Base):
    """
    Sabse important table. Har column ki wajah neeche likhi hai.
    """

    __tablename__ = "seats"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )

    # Seat ka pata: "A" row, seat 12
    row_label: Mapped[str] = mapped_column(String(4))
    seat_number: Mapped[int] = mapped_column(Integer)

    price: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

    # available | locked | booked
    status: Mapped[str] = mapped_column(String(16), default=SEAT_AVAILABLE, index=True)

    # ---- OPTIMISTIC LOCKING ----
    # Har successful update pe +1 hota hai.
    #
    # Do log ek saath seat book karein:
    #   dono version=3 padhte hain
    #   dono UPDATE ... WHERE id=? AND version=3 chalate hain
    #   pehla jeetta hai, version 4 ho jata hai
    #   dusre ka WHERE ab match nahi karta -> rowcount 0 -> usko 409 milta hai
    #
    # Ye "optimistic" isliye hai kyunki hum row lock nahi karte (jo dheema hota
    # hai) — bas maan ke chalte hain ki clash kam hoga, aur clash hone par
    # detect kar lete hain.
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ---- Phase 4 (Redis) ke liye ----
    # Asli lock Redis me hoga (fast). Ye columns sirf "kiske paas hai aur kab tak"
    # ka record rakhte hain, taaki Redis down ho to bhi history rahe.
    locked_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    event: Mapped["Event"] = relationship(back_populates="seats")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="seat")

    __table_args__ = (
        # Ek event me ek hi "A-12" ho sakti hai.
        # Ye database ka niyam hai — seed script me bug ho ya API me,
        # duplicate seat ban hi nahi sakti.
        UniqueConstraint("event_id", "row_label", "seat_number", name="uq_seat_position"),

        # status me sirf teen value hi ja sakti hain. Typo ("Booked", "bookd")
        # database hi reject kar dega.
        CheckConstraint(
            f"status IN ('{SEAT_AVAILABLE}', '{SEAT_LOCKED}', '{SEAT_BOOKED}')",
            name="ck_seat_status",
        ),

        # Seat grid load karte waqt sabse common query: "is event ki saari seats"
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="bookings")
    seat: Mapped["Seat"] = relationship(back_populates="bookings")

    __table_args__ = (
        CheckConstraint(
            f"status IN ('{BOOKING_PENDING}', '{BOOKING_CONFIRMED}', '{BOOKING_CANCELLED}')",
            name="ck_booking_status",
        ),

        # ---- OVERSELLING KA AAKHRI TAALA ----
        # Partial unique index: ek seat ki sirf EK confirmed booking ho sakti hai.
        # Cancelled bookings pe ye lagu nahi hota, isliye seat cancel hone ke baad
        # dubara bik sakti hai.
        #
        # Ye sabse strong guarantee hai. Redis down ho, version check me bug ho,
        # do server ek saath chalein — Postgres phir bhi dusri confirmed booking
        # insert nahi hone dega. IntegrityError aayega, jise hum 409 me badal denge.
        Index(
            "uq_one_confirmed_booking_per_seat",
            "seat_id",
            unique=True,
            postgresql_where=text(f"status = '{BOOKING_CONFIRMED}'"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Booking user={self.user_id} seat={self.seat_id} {self.status}>"

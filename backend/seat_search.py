"""
Seat search — filters se seats dhoondhna.

---- Yahan LLM NAHI hai ----

Natural language wala hissa `ai.py` me hai, aur wo sirf itna karta hai:

    "3 seats together under 1500 near the stage"
                    |
                    v
    SeatFilters(quantity=3, together=True, max_price=1500,
                row_preference="front")

Uske BAAD ka poora kaam yahan hota hai, aur wo bilkul normal code hai —
koi model, koi API call, koi randomness.

Ye bantwara jaan-boojh ke hai aur is feature ka sabse zaroori design
faisla bhi:

  1. **Security.** LLM ka output kabhi SQL nahi banta. Wo ek validated
     Pydantic object banta hai, aur query hamesha parameterised rehti hai.
     Prompt injection zyada se zyada ajeeb filters bana sakta hai —
     data leak ya SQL injection nahi.

  2. **Testability.** Search ka poora logic bina API key ke test hota hai.
     90 me se ek bhi test ko Gemini ki zaroorat nahi.

  3. **Reliability.** Model down ho, key na ho, rate limit lage — search
     phir bhi chalta hai. Sirf natural language wala input band hota hai,
     normal filters nahi.
"""

from dataclasses import dataclass

from models import SEAT_AVAILABLE


@dataclass(frozen=True)
class SeatCandidate:
    """Ek match — ek ya kai seats jo saath me hain."""

    seat_ids: list[int]
    row_label: str
    section: str | None
    seat_numbers: list[int]
    total_price: float

    @property
    def label(self) -> str:
        nums = self.seat_numbers
        if len(nums) == 1:
            return f"{self.row_label}-{nums[0]}"
        return f"{self.row_label}-{nums[0]}…{nums[-1]}"


def _aisle_positions(layout: dict | None) -> dict[str, set[int]]:
    """Row label -> kis seat ke baad aisle hai."""
    out: dict[str, set[int]] = {}
    if not layout:
        return out
    for section in layout.get("sections", []):
        for row in section.get("rows", []):
            gaps = row.get("aisles_after") or []
            if gaps:
                out[str(row["label"]).upper()] = set(gaps)
    return out


def _runs(seats: list, quantity: int, aisles: set[int]) -> list[list]:
    """
    Ek row me `quantity` LAGATAR available seats ke saare groups.

    ⚠️ Aisle "together" ko todti hai.

    Seat 5 aur 6 ke beech agar chalne ka raasta hai, to wo saath nahi
    baithe — beech me log guzar rahe honge. Numbers lagatar hone se ye
    faisla nahi hota, aur yahi wo detail hai jo Phase 18 ka layout data
    kaam me laati hai.

    Bina is check ke search "saath wali seats" bata deta jo asal me
    saath hoti hi nahi — aur wo galti user ko venue me pahunch kar pata
    chalti.
    """
    out = []
    run: list = []

    for seat in seats:
        if run:
            prev = run[-1]
            broken = (
                seat.seat_number != prev.seat_number + 1     # beech me seat gayab/booked
                or prev.seat_number in aisles                 # beech me aisle
            )
            if broken:
                run = []

        run.append(seat)

        if len(run) >= quantity:
            out.append(run[-quantity:])

    return out


def _row_rank(row_label: str, preference: str | None) -> list[int]:
    """
    Sort key — preference ke hisaab se.

    Row A stage ke sabse paas hai (Phase 3 se yahi convention hai), to
    "front" matlab A se shuru aur "back" matlab ulta.

    Character codes ki list lauta rahe hain, string nahi — taki "A" aur
    "A1" jaise labels bhi theek se sort hon.
    """
    sign = -1 if preference == "back" else 1
    return [sign * ord(c) for c in row_label]


def find(
    seats: list,
    *,
    quantity: int = 1,
    together: bool = True,
    min_price: float | None = None,
    max_price: float | None = None,
    section: str | None = None,
    row_preference: str | None = None,
    layout: dict | None = None,
    limit: int = 12,
) -> list[SeatCandidate]:
    """
    Filters se matching seat groups dhoondo.

    Sab kuch memory me hota hai, ek SQL query ke baad. Wajah: "N lagatar
    available seats" ko SQL me likhna window functions ka pahaad ban jata
    hai, aur ek event me max 2000 seats hain — Python me ye kuch
    milliseconds ka kaam hai.

    Agar kabhi 100k seats hue to ye badalna padega, par abhi wo optimise
    karna hoga jo problem hai hi nahi.
    """
    quantity = max(1, min(quantity, 10))

    usable = [s for s in seats if s.status == SEAT_AVAILABLE]

    # `price` BASE hai; user jo dekh raha hai wo current price hai
    # (Phase 14). Filter usi par lagna chahiye jo screen pe dikh raha hai.
    #
    # ⚠️ `or` se nahi, `is None` se check karte hain — free seat (price 0)
    # falsy hoti hai aur `or` usse chupchaap base price pe bhej deta.
    def price_of(seat) -> float:
        display = getattr(seat, "_display_price", None)
        return float(seat.price if display is None else display)

    if min_price is not None:
        usable = [s for s in usable if price_of(s) >= min_price]
    if max_price is not None:
        usable = [s for s in usable if price_of(s) <= max_price]
    if section:
        want = section.strip().lower()
        usable = [s for s in usable if (s.section or "").lower() == want]

    aisles = _aisle_positions(layout)

    by_row: dict[str, list] = {}
    for seat in usable:
        by_row.setdefault(seat.row_label, []).append(seat)

    candidates: list[SeatCandidate] = []

    for row_label, row_seats in by_row.items():
        row_seats.sort(key=lambda s: s.seat_number)

        if quantity == 1 or not together:
            # Saath ki zaroorat nahi — har seat apne aap me ek match hai.
            #
            # ⚠️ `together=False` ke saath quantity > 1 par bhi hum SINGLE
            # seats lautate hain, group nahi. Kyunki "3 seats, saath nahi
            # chahiye" ka matlab hai "koi bhi 3 dikha do" — unhe
            # artificially group karna jhooth hoga.
            groups = [[s] for s in row_seats]
        else:
            groups = _runs(row_seats, quantity, aisles.get(row_label.upper(), set()))

        for group in groups:
            candidates.append(
                SeatCandidate(
                    seat_ids=[s.id for s in group],
                    row_label=row_label,
                    section=group[0].section,
                    seat_numbers=[s.seat_number for s in group],
                    total_price=sum(price_of(s) for s in group),
                )
            )

    # Sasta pehle — par preference diya ho to row order jeetti hai
    if row_preference in ("front", "back"):
        candidates.sort(key=lambda c: (_row_rank(c.row_label, row_preference), c.total_price))
    else:
        candidates.sort(key=lambda c: (c.total_price, c.row_label, c.seat_numbers[0]))

    return candidates[:limit]

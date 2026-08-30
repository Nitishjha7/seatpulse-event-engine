"""
Dynamic pricing — demand ke hisaab se price.

Airlines, Uber, aur concert tickets sab yahi karte hain: jaise-jaise
inventory khatam hoti hai, price badhta hai.

---- Design ka sabse important faisla ----

Seat ka `price` column **kabhi nahi badalta**. Wo BASE price hai.

Current price hamesha calculate hota hai:  base × multiplier

Kyu: agar hum seats.price ko update karte rehte, to
  - purani bookings ka reference toot jata (unhone alag price di thi)
  - har booking par 100 rows update karni padti
  - "base price kya tha" ka jawab kahin nahi bachta
  - do parallel bookings price update pe hi race karti

Ab base immutable hai, multiplier ek chhota calculation hai, aur har
booking apni `amount` khud store karti hai. History bhi safe, aur
performance bhi.

---- Formula ----

    sold_ratio = booked_seats / total_seats
    multiplier = 1 + (sold_ratio × demand_factor)
    multiplier = min(multiplier, max_surge)

    current_price = round(base × multiplier)

demand_factor = 0.5 ka matlab: 100% bik jaane par price 1.5× ho jayega.
Beech me linear badhta hai.

Ye jaan-boojh ke SIMPLE hai. Asli surge pricing me time-to-event, booking
velocity, aur historical demand bhi hota hai — par wo sab bina asli data
ke sirf andaza hoga. Ye formula transparent hai: user ko exactly bata
sakte hain ki price kyu badha.
"""

from dataclasses import dataclass

# Price kis multiple me round karein.
# ₹10 pe round karte hain — ₹827.43 jaisa price bhaddha lagta hai aur
# user ko lagta kuch gadbad hai.
ROUND_TO = 10


@dataclass(frozen=True)
class PricingInfo:
    """Ek event ki abhi ki pricing state."""

    enabled: bool
    multiplier: float
    sold_ratio: float
    sold: int
    total: int
    # Agli price badhne se pehle kitni seats bachi hain.
    # None = pricing off hai, ya max surge pe pahunch chuke hain.
    seats_until_increase: int | None

    @property
    def surge_percent(self) -> int:
        """Kitne percent upar hai base se — UI me dikhane ke liye."""
        return round((self.multiplier - 1) * 100)


def multiplier_for(sold: int, total: int, demand_factor: float, max_surge: float) -> float:
    """
    Demand se multiplier.

    Alag function isliye ki ise test kar sakein aur "kitni seats me price
    badhega" wala calculation isi ko baar-baar call kar sake.
    """
    if total <= 0:
        return 1.0

    sold_ratio = min(1.0, sold / total)
    return min(max_surge, 1.0 + sold_ratio * demand_factor)


def apply(base_price: float, multiplier: float) -> float:
    """Base price pe multiplier lagao aur round karo."""
    raw = base_price * multiplier
    return float(round(raw / ROUND_TO) * ROUND_TO)


def current_price(base_price: float, info: PricingInfo) -> float:
    if not info.enabled:
        return float(base_price)
    return apply(base_price, info.multiplier)


def _seats_until_increase(
    sold: int, total: int, demand_factor: float, max_surge: float, sample_base: float
) -> int | None:
    """
    Kitni aur seats bikne par price badhega.

    ⚠️ Ye ek ANDAZA hai, ek sample base price par. Alag price tiers alag
    points par badhenge (₹800 wali pehle, ₹2500 wali baad me). UI me isse
    "N seats left at this price" ki tarah dikhate hain — jo is tier ke
    liye sach hai.

    Loop bounded hai (bachi hui seats tak), aur seats 2000 tak hi ho sakti
    hain, to ye sasta hai. Har request pe chalta hai, koi cache nahi —
    kyunki galat cached price dikhana isse kahin bura hoga.
    """
    if total <= 0 or sold >= total:
        return None

    now = apply(sample_base, multiplier_for(sold, total, demand_factor, max_surge))

    for extra in range(1, total - sold + 1):
        later = apply(sample_base, multiplier_for(sold + extra, total, demand_factor, max_surge))
        if later > now:
            return extra

    # Max surge pe pahunch gaye, ya rounding ki wajah se price aur nahi badhega
    return None


def pricing_for_event(
    *,
    enabled: bool,
    sold: int,
    total: int,
    demand_factor: float,
    max_surge: float,
    sample_base: float = 1000.0,
) -> PricingInfo:
    """Event ki poori pricing state ek jagah."""
    if not enabled:
        return PricingInfo(
            enabled=False,
            multiplier=1.0,
            sold_ratio=0.0 if total <= 0 else sold / total,
            sold=sold,
            total=total,
            seats_until_increase=None,
        )

    return PricingInfo(
        enabled=True,
        multiplier=multiplier_for(sold, total, demand_factor, max_surge),
        sold_ratio=0.0 if total <= 0 else min(1.0, sold / total),
        sold=sold,
        total=total,
        seats_until_increase=_seats_until_increase(
            sold, total, demand_factor, max_surge, sample_base
        ),
    )

"""
Seat claim karne ke do tareeke — benchmark ke liye.

Poore project me maine OPTIMISTIC locking use kiya hai. Ye file wo daawa
maapne ke liye hai: dusra tareeka (pessimistic, `SELECT ... FOR UPDATE`)
bhi implement karke dono ko same load pe chalate hain.

---- Do tareeke, ek line me ----

    OPTIMISTIC  — "koshish karo, takra gaye to haar maan lo"
                  UPDATE ... WHERE version = <jo maine padha tha>
                  rowcount 0 aaya matlab koi aur jeet gaya. TURANT fail.

    PESSIMISTIC — "pehle taala lagao, phir aaram se karo"
                  SELECT ... FOR UPDATE  -> row lock, doosre RUKTE hain
                  jab tak main commit na karun.

---- Correctness dono me same hai ----

Ye zaroori baat hai: dono overselling rokte hain. Ye correctness ka
mukabla nahi hai, **behaviour under contention** ka hai:

    Optimistic   -> haarne wala TURANT 409 leke chala jata hai
    Pessimistic  -> haarne wala QATAAR me lagta hai, apni baari aane par
                    pata chalta hai ki seat ja chuki, phir 409 milta hai

Matlab optimistic me "fail fast", pessimistic me "wait then fail".
500 users ek seat pe hon to ye farak bahut bada ho jata hai — aur wahi
benchmark maapta hai.

---- ⚠️ Ye code sirf benchmark ke liye chalta hai ----

Pessimistic path tabhi reachable hai jab `settings.BENCHMARK_MODE` on ho.
Production me hamesha optimistic chalta hai. Wajah neeche
`routers/bookings.py` me likhi hai.
"""

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from models import SEAT_AVAILABLE, SEAT_BOOKED, SEAT_LOCKED, Seat

OPTIMISTIC = "optimistic"
PESSIMISTIC = "pessimistic"

_CLAIMABLE = (SEAT_AVAILABLE, SEAT_LOCKED)

# Booked karte waqt jo values set karni hain — dono strategies me bilkul
# same, warna benchmark do alag cheezein maap raha hota.
_CLAIM_VALUES = {
    "status": SEAT_BOOKED,
    "locked_by": None,
    "locked_until": None,
    "held_price": None,
}


@dataclass
class ClaimResult:
    won: bool
    # Haarne par kyu — dono strategies me wajah alag alag hoti hai, aur
    # yahi farak benchmark ki asli kahani hai.
    reason: str | None = None


def claim_optimistic(db: Session, seat_id: int, expected_version: int) -> ClaimResult:
    """
    Ek atomic UPDATE. Koi lock nahi, koi wait nahi.

    Poora faisla WHERE clause me hai: version wahi hona chahiye jo maine
    padha tha. Do parallel requests me sirf EK ka WHERE match karega,
    doosre ko 0 rows milengi — aur wo turant nikal jayega.

    Isi ko "optimistic" kehte hain: hum maan ke chalte hain ki takrav
    nahi hoga, aur ho gaya to detect karke haar maan lete hain.
    """
    result = db.execute(
        update(Seat)
        .where(
            Seat.id == seat_id,
            Seat.version == expected_version,
            Seat.status.in_(_CLAIMABLE),
        )
        .values(version=Seat.version + 1, **_CLAIM_VALUES)
        .execution_options(synchronize_session=False)
    )

    if result.rowcount == 0:
        return ClaimResult(False, "version_conflict")
    return ClaimResult(True)


def claim_pessimistic(db: Session, seat_id: int) -> ClaimResult:
    """
    Pehle row lock lo, phir check karo, phir update karo.

    ⚠️ `with_for_update()` wali line BLOCK karti hai. Jab tak lock rakhne
    wali transaction commit/rollback na kare, ye request yahin khadi
    rehti hai.

    Aur khadi rehne ka matlab sirf "der" nahi hai — ye request tab tak
    apna **database connection pakde** rehti hai. 500 users ek seat pe
    hon to 499 connections qataar me atke rehte hain, jabki pool me sirf
    40 hain. Wahi se pool exhaustion shuru hota hai.
    (Bilkul wahi bimari jo Phase 7 me pakdi thi, alag wajah se.)

    Optimistic version me `version` column ki zaroorat padti hai. Yahan
    nahi — row lock hi guarantee de deta hai ki beech me koi ghusa nahi.
    Phir bhi hum version badhate hain, taki WebSocket clients ko pata
    chale ki seat badli (aur dono strategies ka data identical rahe).
    """
    seat = db.execute(
        select(Seat).where(Seat.id == seat_id).with_for_update()
    ).scalar_one_or_none()

    if seat is None:
        return ClaimResult(False, "not_found")

    # ⭐ Ye check lock MILNE KE BAAD hai, pehle nahi.
    #
    # Yahi poora point hai: jab tak main yahan pahuncha, ho sakta hai
    # jisne mujhe rok rakha tha usne seat book kar li ho. Isliye status
    # dobara padhna zaroori hai — aur ab wo padhai bharosemand hai,
    # kyunki row mere lock me hai.
    if seat.status not in _CLAIMABLE:
        return ClaimResult(False, f"already_{seat.status}")

    seat.version += 1
    for field, value in _CLAIM_VALUES.items():
        setattr(seat, field, value)

    return ClaimResult(True)

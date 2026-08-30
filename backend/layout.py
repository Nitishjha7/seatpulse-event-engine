"""
Seat layout — venue ka naksha, aur usse seats banane ka kaam.

---- Ab tak kya tha ----

Phase 10 se organizer `price_tiers` se event banata hai: "2 rows @ ₹2500,
3 rows @ ₹1200", aur har row me utni hi seats. Simple hai, aur zyadatar
events ke liye kaafi bhi.

Par asli venue aisa nahi hota:
  - beech me AISLE hoti hai (chalne ka raasta)
  - alag SECTIONS hote hain (Ground, Balcony) — alag pricing, alag naam
  - har row me barabar seats nahi hoti (aage kam, peeche zyada)

Ye file wo layout describe karne aur usse seats banane ka kaam karti hai.

---- Purana tarika HATAYA nahi ----

`price_tiers` abhi bhi chalta hai. Wajah:

  1. 17 phases ka data usi se bana hai. Usse todna matlab purane events
     ka seed, tests, aur demo sab todna.
  2. Zyadatar events ko sach me layout builder ki zaroorat nahi. "5 rows,
     10 seats, ek price" ke liye naksha banwana user ko sataana hai.

Isliye dono raaste hain, aur dono ke aakhir me WAHI seats banti hain.
Layout wale events me bas `Event.layout` bhi bhara hota hai, taki grid
aisles aur sections dikha sake.

---- Layout ka shape ----

    {
      "sections": [
        {
          "name": "Ground",
          "price": 2500,
          "rows": [
            {"label": "A", "seats": 10, "aisles_after": [4]},
            {"label": "B", "seats": 12}
          ]
        }
      ]
    }

`aisles_after: [4]` matlab seat 4 ke BAAD ek gap. Ye gap sirf DIKHNE ka
hai — koi seat nahi banti, koi number skip nahi hota. Purely presentation,
isliye layout JSON me hai, seats table me nahi.
"""

from dataclasses import dataclass

# Wahi limits jo price_tiers wale raaste me hain — dono ka behaviour
# alag nahi hona chahiye.
MAX_SEATS_PER_EVENT = 2000
MAX_SECTIONS = 10
MAX_ROWS_PER_SECTION = 40
MAX_SEATS_PER_ROW = 60
MAX_LABEL_LEN = 4


class LayoutError(ValueError):
    """Layout galat hai — route ise 422 me badalta hai."""


@dataclass(frozen=True)
class PlannedSeat:
    section: str
    row_label: str
    seat_number: int
    price: float


def validate(layout: dict) -> None:
    """
    Layout theek hai ya nahi — seats banane se PEHLE.

    ⚠️ Ye server par chalta hai, chahe frontend ne kitna bhi check kiya ho.
    Layout builder ek UI convenience hai; koi bhi seedha API ko kachra
    bhej sakta hai.

    Alag function isliye ki ise test kar sakein bina DB ke, aur taki
    "kya galat hai" ka jawab expansion se pehle mile — aadhi seats ban
    jaane ke baad nahi.
    """
    sections = layout.get("sections")
    if not isinstance(sections, list) or not sections:
        raise LayoutError("Kam se kam ek section chahiye")

    if len(sections) > MAX_SECTIONS:
        raise LayoutError(f"Max {MAX_SECTIONS} sections")

    seen_labels: set[str] = set()
    seen_sections: set[str] = set()
    total = 0

    for i, section in enumerate(sections):
        name = str(section.get("name", "")).strip()
        if not name:
            raise LayoutError(f"Section {i + 1} ka naam khali hai")
        if len(name) > 40:
            raise LayoutError(f"Section ka naam bahut lamba: {name[:20]}…")
        if name in seen_sections:
            raise LayoutError(f"Do sections ka naam ek hi hai: {name}")
        seen_sections.add(name)

        price = section.get("price")
        if not isinstance(price, (int, float)) or price < 0 or price > 1_000_000:
            raise LayoutError(f"'{name}' ka price theek nahi")

        rows = section.get("rows")
        if not isinstance(rows, list) or not rows:
            raise LayoutError(f"'{name}' me kam se kam ek row chahiye")
        if len(rows) > MAX_ROWS_PER_SECTION:
            raise LayoutError(f"'{name}' me max {MAX_ROWS_PER_SECTION} rows")

        for row in rows:
            label = str(row.get("label", "")).strip().upper()
            if not label:
                raise LayoutError(f"'{name}' me ek row ka label khali hai")
            if len(label) > MAX_LABEL_LEN:
                raise LayoutError(f"Row label bahut lamba: {label}")

            # ⭐ Ye sabse zaroori check hai.
            #
            # `seats` table par UNIQUE(event_id, row_label, seat_number) hai.
            # Do sections me same row label ho to expansion IntegrityError
            # se marega — aur wo error tab aayega jab hum 500 seats insert
            # kar chuke honge. Yahan pakadna kahin behtar hai.
            #
            # Note: ye poore EVENT me unique hona chahiye, sirf section me
            # nahi — kyunki constraint section ko jaanta hi nahi.
            if label in seen_labels:
                raise LayoutError(
                    f"Row '{label}' do jagah hai — har row label poore event me alag hona chahiye"
                )
            seen_labels.add(label)

            count = row.get("seats")
            if not isinstance(count, int) or count < 1 or count > MAX_SEATS_PER_ROW:
                raise LayoutError(f"Row '{label}' me 1-{MAX_SEATS_PER_ROW} seats honi chahiye")

            aisles = row.get("aisles_after", [])
            if not isinstance(aisles, list):
                raise LayoutError(f"Row '{label}' ka aisles_after list hona chahiye")
            for a in aisles:
                # Aakhri seat ke baad aisle ka koi matlab nahi — wo row ka
                # ant hai. Ise chupchaap ignore karne ke bajaye bata dete
                # hain, warna organizer ko lagta rehta ki aisle bani hai.
                if not isinstance(a, int) or a < 1 or a >= count:
                    raise LayoutError(
                        f"Row '{label}': aisle position {a} row ke andar honi chahiye (1-{count - 1})"
                    )

            total += count

    if total > MAX_SEATS_PER_EVENT:
        raise LayoutError(f"Max {MAX_SEATS_PER_EVENT} seats — is layout me {total} hain")


def expand(layout: dict) -> list[PlannedSeat]:
    """
    Layout se seats ki poori list banao.

    ⚠️ Ye seats DB me nahi likhta — sirf list lauta deta hai.

    Wajah: caller ise ek transaction ke andar bulk insert karta hai. Agar
    ye khud likhta, to "aadhi seats ban gayi phir error" wali haalat
    mumkin ho jati. Ab expansion pure hai aur poori list ek saath insert
    hoti hai — ya sab, ya kuch nahi.
    """
    validate(layout)

    seats: list[PlannedSeat] = []
    for section in layout["sections"]:
        name = str(section["name"]).strip()
        price = float(section["price"])
        for row in section["rows"]:
            label = str(row["label"]).strip().upper()
            for n in range(1, int(row["seats"]) + 1):
                seats.append(PlannedSeat(name, label, n, price))
    return seats


def summarise(layout: dict) -> dict:
    """
    Layout ka quick summary — seats banaye bina.

    Organizer form isse live preview dikhata hai ("3 sections · 240 seats ·
    ₹800–₹2500"), aur validation error bhi yahin se aata hai. Isse UI ko
    layout ki structure samajhne ki zaroorat nahi padti.
    """
    try:
        validate(layout)
    except LayoutError as exc:
        return {"valid": False, "error": str(exc)}

    prices = [float(s["price"]) for s in layout["sections"]]
    total = sum(
        int(r["seats"]) for s in layout["sections"] for r in s["rows"]
    )

    return {
        "valid": True,
        "sections": len(layout["sections"]),
        "rows": sum(len(s["rows"]) for s in layout["sections"]),
        "total_seats": total,
        "min_price": min(prices),
        "max_price": max(prices),
    }


def from_price_tiers(tiers: list[dict], seats_per_row: int, row_labels: str) -> dict:
    """
    Purane `price_tiers` ko layout ke shape me badlo.

    Isse do faayde hain:
      - dono raaste ek hi expansion code use karte hain, do nahi
      - purane style se bana event bhi grid me sections dikha sakta hai

    Har tier ek section ban jata hai. Naam automatic — organizer ne diya
    hi nahi tha.
    """
    sections = []
    row_index = 0
    for i, tier in enumerate(tiers, start=1):
        rows = []
        for _ in range(int(tier["rows"])):
            rows.append({"label": row_labels[row_index], "seats": seats_per_row})
            row_index += 1
        sections.append({
            "name": f"Tier {i}" if len(tiers) > 1 else "General",
            "price": float(tier["price"]),
            "rows": rows,
        })
    return {"sections": sections}

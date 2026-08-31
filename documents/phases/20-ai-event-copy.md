# Phase 20 — AI Event Copy

> Organizer ek line likhta hai — "Arijit Singh concert, DY Patil Mumbai,
> December" — aur listing ka draft ban jata hai.
>
> Par is phase ka asli sawaal ye hai: **AI ko kya likhne dena chahiye,
> aur kya nahi.**

---

## ⚠️ Pehle: poster generator NAHI bana

README ka original plan tha "AI event copy **+ poster generator**".
Poster wala hissa **nahi banaya**, aur wajah likh dena zaroori hai:

```
gemini-3.1-flash-image        429
gemini-3.1-flash-lite-image   429
gemini-3.1-flash-image-preview 429
gemini-2.5-flash-image        429
gemini-3-pro-image            429

"You exceeded your current quota"
```

Free tier me image generation ka quota hai hi nahi. Text models usi key
se bilkul theek chal rahe hain.

To do raaste the: feature likh ke "ban gaya" bol dena (jo main verify hi
nahi kar sakta), ya na banana aur wajah likhna. **Doosra chuna.** Ek
aisa feature jo maine kabhi chalte hue dekha hi nahi, wo README me jhooth
hai — aur interview me wahi sabse pehle poocha jata hai.

Paid tier par ye jodna aasan hoga: wahi `httpx` call, response me
`inlineData` (base64 image) aati hai, aur usse ek volume me store karna
hota. Par abhi wo **nahi** hai.

---

## ⭐ Asli design faisla: AI ko publish button tak nahi pahunchne dete

```
brief  ->  Gemini  ->  DRAFT  ->  form ke fields  ->  organizer edit  ->  publish
                                                              ^
                                                    yahan insaan hai
```

Endpoint kuch **save nahi karta**. Wo sirf ek suggestion lautata hai jo
organizer ke form me bhar jati hai.

**Wajah cosmetic nahi hai:**

> Event ka description ticket kharidne wale ke liye ek **waada** hai.
>
> Model "featuring special guests" ya "3-hour show with intermission"
> gadh de, aur wo bina padhe publish ho jaye — to jhooth attendee tak
> pahunch jata hai. Aur uska zimmedar organizer hota hai, AI nahi.

Test isi ko pin karta hai:

```python
def test_draft_does_not_create_an_event(...):
    before = len(organizer_ke_events)
    client.post("/api/organizer/events/draft", ...)
    after = len(organizer_ke_events)
    assert after == before
```

---

## ⭐⭐ Prompt ka sabse zaroori hissa: facts mat gadho

```
⚠️ SABSE ZAROORI NIYAM: koi bhi fact MAT gadho.

Sirf wahi cheezein likho jo user ne batayi hain. Ye sab MANA hai:
- lineup, guest artists, opening acts
- show ki duration, interval, timing
- ticket price, offers, discounts
- ratings, "sold out", "trending", ya koi bhi ginti
- awards, past shows, reviews
```

Ye list yun hi nahi banayi — ye **wahi cheezein hain jo ek marketing LLM
sabse pehle gadhta hai**, kyunki wo "achhi listing" jaisi lagti hain.

Aur ye poore project ke us stance se judta hai jo shuru se hai: README me
kabhi "50K+ users" ya "4.8★ 12.5K reviews" nahi likha, kyunki wo sach
nahi tha. Ab wahi niyam model par bhi lagta hai.

### Chal ke dekha

```
BRIEF: Arijit Singh concert, DY Patil Stadium Mumbai, December

{
  "name": "Arijit Singh Live in Mumbai",
  "description": "Experience a live musical performance by Arijit Singh.
     Join us for an unforgettable evening filled with soulful melodies and
     popular tracks in the heart of the city.\n\nThe event will be hosted at
     the DY Patil Stadium in Mumbai this December. Please make sure to reach
     the venue in advance to enjoy the concert experience.",
  "category": "Music"
}
```

Koi lineup nahi, koi duration nahi, koi price nahi, koi rating nahi.
"Soulful melodies" generic marketing hai — wo factual claim nahi hai.

---

## `temperature` — yahan 0.8, search me 0

Ye [Phase 19](19-nl-seat-search.md) se ulta hai, aur jaan-boojh ke:

| | temperature | Kyu |
|---|---|---|
| Search parser | **0** | Ek input ka hamesha ek hi jawab aana chahiye. Randomness wahan bug hai. |
| Copy draft | **0.8** | Do baar chalane par alag options milna **faayda** hai. Organizer ko pasand na aaye to dobara dabaye. |

Ek hi `ai.py` me dono hain, aur unke reasons alag likhe hain — warna kal
koi "consistency" ke naam par dono ko ek jaisa kar dega.

---

## Ek chhota bug jo test karke mila

Pehla output aisa aaya:

```
"description": "Arijit Singh ke saath ek shaam ka anand lein. Yeh live
  concert sangeet premion ke liye ek vishesh avsar hai..."
```

Brief **English** me thi, jawab **Hindi transliteration** me aaya.

Wajah: mera system prompt Hinglish me likha hai, to model ne usi ko match
kar liya. Ye listing public hai — organizer ne jis bhasha me socha hai,
uske audience bhi wahi padhte hain.

Fix prompt me:

```
- ⚠️ Brief JIS BHASHA me hai, usi bhasha me likho.
```

> Phase 19 ki tarah yahan bhi: LLM feature ka fix aksar **prompt me** hota
> hai, code me nahi. Aur wo galti sirf asli input chala ke dikhti hai.

---

## Graceful degradation — wahi purana pattern

```jsx
if (!aiSearchEnabled) return null
```

Key na ho to draft box dikhta hi nahi, aur form haath se bharna poori
tarah chalta hai. Wahi pattern jo Google login
([Phase 7](07-auth-google-oauth.md)), Stripe ([Phase 11](11-payments.md))
aur NL search ([Phase 19](19-nl-seat-search.md)) me hai.

Server par bhi saaf jawab, `500` nahi:

| Haalat | Status | Kyu |
|---|---|---|
| Key nahi hai | `503` | Server ka intezaam adhoora hai — client ki galti nahi |
| Model fail hua | `502` | Upstream ki dikkat. Organizer form haath se bhar sakta hai |
| Brief chhoti/badi | `422` | Client ki galti |
| Attendee ne maanga | `403` | Wo event bana hi nahi sakta, to draft bhi nahi |

---

## UI me ek line jo zaroori hai

Draft bharne ke baad:

> **Draft neeche bhar diya hai — publish se pehle padh lo.**
> Jo likha hai wo tumhare naam se attendees tak jayega.

Ye sirf politeness nahi hai. Organizer ko pata hona chahiye ki neeche jo
bhara hai wo ek **mashin** ne likha hai, aur uski zimmedari uski hai.
Bina is line ke wo aasani se maan sakta hai ki "app ne bhara hai to
theek hi hoga".

---

## Proof

```
$ POST /api/organizer/events/draft   (organizer token)
200  {"name":"Zakir Khan Live in Delhi",
      "description":"Zakir Khan is bringing his stand-up comedy performance
        to Delhi this January. Experience his unique storytelling and
        observational humor live on stage.\n\nThe event will be held in
        Delhi. Please ensure you check your tickets for specific venue
        details and entry instructions.",
      "category":"Comedy"}

$ same request, attendee token
403  "Is kaam ke liye organizer ya admin role chahiye"

$ same request, no token
401
```

Latency ~2.4s.

### Tests

**110/110 pass** (105 pehle ke + 5 naye). **Kisi ko API key ki zaroorat nahi.**

- `test_draft_needs_organizer_role`
- `test_draft_needs_auth`
- `test_draft_rejects_empty_or_huge_briefs`
- `test_draft_returns_the_three_form_fields`
- ⭐ `test_draft_does_not_create_an_event`

**Content test nahi karte** — model har baar alag likhega, aur likhna hi
chahiye. Contract test hota hai, prose nahi. Aur test AI off hone par bhi
pass hota hai (`200` ya saaf `502/503` — kabhi `500` nahi).

---

## Jo jaan-boojh ke NAHI banaya

- **Poster generator** — upar wajah likhi hai (free tier quota).
- **Purane event ki copy dobara likhwana.** Draft sirf naye event ke form
  me hai. Publish ho chuke event ka description badalna attendees se kiya
  waada badalna hai.
- **Draft ka koi history nahi.** Dobara dabao to naya draft aata hai,
  purana gaya. Version history rakhna is chhote feature ke liye zyada hai.
- **Venue/date AI se nahi bharte.** Wo asli facts hain, aur unhe organizer
  hi bharega. Model unhe gadh sakta hai — isliye form me wo alag fields
  hain jinhe draft chhoota hi nahi.

---

## Files

**Naye:**
| File | Kya |
|---|---|
| `frontend/src/components/AiDraft.jsx` | Brief box + "publish se pehle padh lo" warning |

**Badle:**
| File | Kya |
|---|---|
| `backend/ai.py` | `draft_event_copy()`, `COPY_PROMPT` (facts mat gadho) |
| `backend/schemas.py` | `EventDraftRequest`, `EventDraftOut` |
| `backend/routers/organizer.py` | `POST /events/draft` — save kuch nahi karta |
| `frontend/src/api.js`, `pages/organizer/CreateEvent.jsx` | Wiring |

---

## Related

- [Phase 19 — NL Seat Search](19-nl-seat-search.md) — AI ki boundary, model choice, key handling
- [Phase 07 — Auth + Google OAuth](07-auth-google-oauth.md) — graceful degradation ka pattern
- [Phase 10 — RBAC + Organizer](10-rbac-organizer.md) — role check jo yahan bhi lagta hai
- [Interview Prep](../interview-prep.md) — "AI ko kitna kaam dena chahiye"

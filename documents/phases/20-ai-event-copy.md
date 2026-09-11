# Phase 20 — AI Event Copy

> An organizer writes one line — "Arijit Singh concert, DY Patil Mumbai, December" — and a listing draft is generated.
>
> But the real question of this phase is: **what should the AI be allowed to write, and what should it not.**

---

## ⚠️ First: I did NOT build a poster generator

The original README plan included "AI event copy **+ poster generator**".
I did **not** build the poster part, and it is important to document why:

```
gemini-3.1-flash-image        429
gemini-3.1-flash-lite-image   429
gemini-3.1-flash-image-preview 429
gemini-2.5-flash-image        429
gemini-3-pro-image            429

"You exceeded your current quota"
```

The free tier has no quota for image generation. Text models are working perfectly with the same key.

There were two paths: claim the feature is "built" (which I cannot verify), or not build it and explain why. **I chose the latter.** A feature I have never seen working is a lie in the README — and that is the first thing asked in an interview.

Adding this in a paid tier would be easy: the same `httpx` call, the response contains `inlineData` (base64 image), and it would need to be stored in a volume. But for now, it is **not** there.

---

## ⭐ Key design decision: Do not let AI reach the publish button

```
brief  ->  Gemini  ->  DRAFT  ->  form fields  ->  organizer edit  ->  publish
                                                              ^
                                                    human is here
```

The endpoint **saves nothing**. It only returns a suggestion that populates the organizer's form.

**The reason is not cosmetic:**

> An event description is a **promise** to the ticket buyer.
>
> If the model invents "featuring special guests" or "3-hour show with intermission," and it is published without being read, a lie reaches the attendee. The organizer is responsible for that, not the AI.

The test pins this down:

```python
def test_draft_does_not_create_an_event(...):
    before = len(organizer_ke_events)
    client.post("/api/organizer/events/draft", ...)
    after = len(organizer_ke_events)
    assert after == before
```

---

## ⭐⭐ The most important part of the prompt: Do not invent facts

```
⚠️ MOST IMPORTANT RULE: Do NOT invent any facts.

Only write what the user has provided. All of the following are FORBIDDEN:
- lineup, guest artists, opening acts
- show duration, interval, timing
- ticket price, offers, discounts
- ratings, "sold out", "trending", or any counts
- awards, past shows, reviews
```

This list was not created randomly — these are **the exact things a marketing LLM invents first**, because they make for a "good listing."

This aligns with the project's stance from the beginning: the README never claimed "50K+ users" or "4.8★ 12.5K reviews" because it wasn't true. Now, the same rule applies to the model.

### Testing it

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

No lineup, no duration, no price, no rating. "Soulful melodies" is generic marketing — it is not a factual claim.

---

## `temperature` — 0.8 here, 0 for search

This is the opposite of [Phase 19](19-nl-seat-search.md), and intentionally so:

| | temperature | Why |
|---|---|---|
| Search parser | **0** | One input must always yield the same answer. Randomness is a bug there. |
| Copy draft | **0.8** | Getting different options on two runs is a **benefit**. If the organizer doesn't like it, they can try again. |

Both are in the same `ai.py`, with their reasons documented separately — otherwise, someone might change both to be the same in the name of "consistency."

---

## A small bug found during testing

The first output was:

```
"description": "Arijit Singh ke saath ek shaam ka anand lein. Yeh live
  concert sangeet premion ke liye ek vishesh avsar hai..."
```

The brief was in **English**, but the response came in **Hindi transliteration**.

Reason: My system prompt was written in Hinglish, so the model matched that. This listing is public — the audience reads the language the organizer thinks in.

Fix in the prompt:

```
- ⚠️ Write in the SAME LANGUAGE as the brief.
```

> Like Phase 19: an LLM feature fix is often in the **prompt**, not the code. And that error only appears when running real input.

---

## Graceful degradation — the same old pattern

```jsx
if (!aiSearchEnabled) return null
```

If the key is missing, the draft box does not appear, and filling the form manually works perfectly. This is the same pattern used for Google login ([Phase 7](07-auth-google-oauth.md)), Stripe ([Phase 11](11-payments.md)), and NL search ([Phase 19](19-nl-seat-search.md)).

Clear responses on the server, not `500`:

| Status | Code | Why |
|---|---|---|
| Key missing | `503` | Server setup is incomplete — not the client's fault |
| Model failure | `502` | Upstream issue. Organizer can fill the form manually |
| Brief too short/long | `422` | Client error |
| Attendee requested | `403` | They cannot create an event, so they cannot draft one |

---

## A necessary line in the UI

After filling the draft:

> **Draft has been filled below — read it before publishing.**
> What is written will go to attendees under your name.

This is not just politeness. The organizer must know that what is filled below was written by a **machine**, and they are responsible for it. Without this line, they could easily assume "if the app filled it, it must be correct."

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
403  "Organizer or admin role required for this action"

$ same request, no token
401
```

Latency ~2.4s.

### Tests

**110/110 pass** (105 previous + 5 new). **No one needs an API key.**

- `test_draft_needs_organizer_role`
- `test_draft_needs_auth`
- `test_draft_rejects_empty_or_huge_briefs`
- `test_draft_returns_the_three_form_fields`
- ⭐ `test_draft_does_not_create_an_event`

**Content is not tested** — the model will write something different every time, and it should. We test the contract, not the prose. The test also passes when AI is off (`200` or clean `502/503` — never `500`).

---

## What was intentionally NOT built

- **Poster generator** — reason documented above (free tier quota).
- **Regenerating copy for old events.** Draft is only in the new event form. Changing the description of a published event is changing a promise made to attendees.
- **Draft history.** Pressing it again provides a new draft; the old one is gone. Version history is overkill for this small feature.
- **Venue/date AI population.** These are real facts, and the organizer must fill them. The model could invent them — which is why they are separate fields in the form that the draft does not touch.

---

## Files

**New:**
| File | Purpose |
|---|---|
| `frontend/src/components/AiDraft.jsx` | Brief box + "read before publishing" warning |

**Modified:**
| File | Purpose |
|---|---|
| `backend/ai.py` | `draft_event_copy()`, `COPY_PROMPT` (do not invent facts) |
| `backend/schemas.py` | `EventDraftRequest`, `EventDraftOut` |
| `backend/routers/organizer.py` | `POST /events/draft` — saves nothing |
| `frontend/src/api.js`, `pages/organizer/CreateEvent.jsx` | Wiring |

---

## Related

- [Phase 19 — NL Seat Search](19-nl-seat-search.md) — AI boundaries, model choice, key handling
- [Phase 07 — Auth + Google OAuth](07-auth-google-oauth.md) — graceful degradation pattern
- [Phase 10 — RBAC + Organizer](10-rbac-organizer.md) — role check applied here as well
- [Interview Prep](../interview-prep.md) — "How much work should be delegated to AI"

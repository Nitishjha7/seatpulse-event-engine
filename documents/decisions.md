# Key decisions

Quick reference for design choices that aren't obvious from just reading the code, and why I went that way instead of the alternative.

| Decision | Alternative considered | Why I picked this |
|---|---|---|
| Optimistic locking (version column) for single-seat booking | `SELECT ... FOR UPDATE` on every booking | Most concurrent traffic isn't fighting over the same row — locking only when a conflict actually happens is cheaper than locking every time on the chance one might |
| `SELECT ... FOR UPDATE` for group booking confirm/expiry | Optimistic version check, same as single-seat | Confirm and expiry both need to agree on one shared parent row; letting whichever gets there first win and making the other back off is simpler than reconciling two optimistic writes after the fact |
| Redis lock as a pre-check, not the source of truth | Trusting Redis alone for seat locking | Redis can go down or a TTL can expire mid-flow. It's there to reject obviously-conflicting requests cheaply, not to be the thing correctness depends on |
| Rate limiting keyed on user ID, not IP | Per-IP limiting | Behind a proxy or NAT, IP-based limits either punish a whole office for one bad actor or get bypassed entirely with a spoofed header |
| Rate limiter fails open when Redis is down | Fail closed (reject all requests) | Rate limiting is a defense, not a correctness guarantee. Booking correctness already has three independent layers that don't depend on Redis being up |
| JWT access token in memory, refresh token in httpOnly cookie | Both in localStorage | An XSS bug that can read localStorage can steal a long-lived refresh token. Keeping it in memory means a page reload loses the access token, but a refresh call using the httpOnly cookie gets a new one — the tradeoff is a page refresh needs one extra round trip, in exchange for the refresh token never being reachable by injected JS |
| Ticket generation in a background worker (ARQ), not inline | Generate the PDF/QR during the booking request | PDF rendering takes a couple seconds. Booking confirmation shouldn't wait on that — the user gets a "confirmed, ticket coming" response immediately and polls or gets notified when it's ready |
| Webhook as source of truth for payment success | Trusting the client's "payment succeeded" redirect | A user can close the tab right after paying, before the redirect fires. The webhook is server-to-server and doesn't depend on the browser still being open |
| Partial unique index on booked seats, even with the version column already in place | Relying on the version column alone | The version column protects against races in application code. The index protects against everything else — a bug, a bad migration, someone hitting the DB directly |
| Gemini used only for text → structured filters, never for raw SQL or seat IDs | Letting the model generate a query directly | Model output goes through a validated Pydantic schema before it touches anything. Worst case with a bad or adversarial prompt is a search that returns nothing useful, not a data leak |
| Optional integrations (Google login, Stripe, Gemini) degrade to hidden/mocked when unconfigured | Requiring all three keys to run the app at all | Nobody evaluating this project should have to go set up three third-party accounts just to see it work |

If you want the full reasoning behind any of these instead of the summary, see [walkthrough.md](walkthrough.md).

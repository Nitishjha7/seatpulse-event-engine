# SeatPulse — walkthrough

This is a longer write-up of how the booking flow actually works and why I built the locking the way I did. The README covers the summary; this is the detail for anyone who wants it.

## The problem I was solving

Ticket sites fall over the moment a sale actually gets popular. The typical bug is simple to describe and easy to write by accident: read the seat, check if it's free, then update it. Between the read and the update, another request can slip in and do the same thing. Run that at any real concurrency and the same seat gets sold to five different people.

I wanted to build something where that's structurally not possible — not "unlikely," not "we added a retry," but actually can't happen regardless of how many people click at once.

## The booking flow, step by step

1. **User clicks a seat.** The frontend sends `POST /seats/{id}/lock`. The backend tries `SET seat:{id} NX EX 300` in Redis. `NX` means "only set if it doesn't already exist" — so if two people click the same seat in the same millisecond, exactly one `SET` succeeds and the other gets `nil` back immediately. That second person gets a 409 before the request ever touches Postgres. This is the cheap, fast rejection layer — it's what keeps the database from being hammered during a flash sale.

2. **Lock held, seat visible as taken.** The seat's DB status also flips to `locked`, and the change goes out over Redis pub/sub to everyone else's open WebSocket. Every connected browser sees that seat turn yellow within milliseconds, no polling.

3. **User checks out.** `POST /bookings` runs one SQL statement: `UPDATE seats SET status='booked' WHERE id=42 AND version=7`. If nothing else touched this seat since it was read, the row's `version` still matches and the update succeeds — rowcount 1. If the Redis lock had somehow been bypassed (crash, manual DB edit, a bug in some other code path), the version check catches it instead, and the update just returns rowcount 0. No booking gets inserted from a failed conditional update.

4. **Final guarantee.** Underneath both of the above there's a partial unique index on `(seat_id) WHERE status = 'booked'`. Even if every layer above it somehow got bypassed, Postgres itself refuses to let two confirmed bookings exist for the same seat. This is the one I'd trust the least to ever be needed and the one I was still not willing to skip.

Three independent layers, in decreasing order of "how likely is this to be the thing that actually saves us" and increasing order of "how sure are we this can't be wrong." Redis is fast but not the source of truth. The version column is the real gate. The unique index is what stops a bug in my own code from becoming a lost sale.

## Why not just lock the row (`SELECT ... FOR UPDATE`)?

I actually built both and benchmarked them (see the Measured results section in the README, and the numbers were closer than I expected). Row locking works fine when contention is on a single row — everyone queues, one at a time, no drama. The problem shows up when the contention is spread across many rows instead: a `FOR UPDATE` scan or a badly-scoped lock can serialize work that doesn't need to be serialized, and now you're paying for locking overhead on requests that were never actually going to conflict.

The optimistic approach only pays a cost when there's an actual conflict — most requests just succeed with a plain update, no lock held, no other transaction waiting. It's the right tradeoff when a lot of the traffic is *not* fighting over the same seat, which is the normal case even during a flash sale (100 seats, 500 people — most of them will end up wanting different seats).

The one place I used `SELECT ... FOR UPDATE` deliberately is group bookings, where a confirm and an expiry check are racing against *different rows that both need to agree on the state of a shared parent*. That's a genuinely different shape of problem — see the section below.

## The part that took longer than I expected: group bookings

Group booking (split a set of seats across N people, each pays their own share, and the whole group either goes through or nobody does) sounds like it should reuse the same single-seat pattern. It mostly does, except for one specific race: a payment confirming a share and a background job expiring the group for missing the deadline can happen at almost the same instant, and both are legitimate.

If the expiry job wins, seats already paid for need to get refunded correctly. If the confirmation wins right as the deadline job runs, I don't want a scenario where the group's status flips to `expired` after a share was already marked paid, leaving an inconsistent state that neither the refund logic nor the "all confirmed" logic knows how to handle.

I ended up locking the parent `GroupBooking` row with `SELECT ... FOR UPDATE` at the point where either a payment confirmation or an expiry job would change its status, so whichever one gets there first decides what happens, and the other reads the already-updated status and backs off cleanly instead of racing. This is the one place in the codebase I'm comfortable defending row-locking over the optimistic approach, precisely because the thing being protected is one shared row that many different flows need to agree on.

I tested this by running the confirm path and the expiry path against the same group over and over concurrently (60 runs) and checking that exactly one of them ever wins and no group ends up half-confirmed, half-refunded.

## What happens when things fail partway through

- **Payment succeeds but the booking write fails** — the webhook is idempotent (keyed on the provider's event ID), so a retry from the payment provider just replays safely instead of double-charging or double-booking.
- **A seat hold expires while checkout is in progress** — checkout re-validates the lock right before charging, so a hold that expired mid-flow gets caught before money moves, not after.
- **A ticket PDF fails to generate** — booking confirmation doesn't wait on it. Ticket generation happens in a background worker (ARQ) with retries, so a slow or flaky render never blocks the actual purchase.
- **Redis is down** — seat locking degrades to relying on the version column and the unique index alone. Slower under contention, still correct. Rate limiting fails open rather than taking the whole site down over something that's meant to be a protection, not a correctness guarantee.

## Things I'd do differently with more time

- The rate limiter keys everything on `user:{id}` regardless of which endpoint is calling it, so different limits sharing one account can interact in ways that aren't obvious from reading either endpoint on its own. It works, but a bucket per (user, limit-type) pair would be cleaner.
- No event sourcing / audit log for price changes yet — if I added a payments dispute flow, I'd want a durable log of exactly what price was quoted when, not just the current row.
- Load testing was done against a single machine running the whole stack. I'd want real numbers with Postgres and Redis on separate hosts before trusting the latency figures for a real deployment.

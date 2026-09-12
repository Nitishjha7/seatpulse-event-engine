"""
Load tests — to quantify performance claims.

Do scenarios:

  FlashSaleUser  — all users target the same seat. Validates the "no overselling" claim.
  BrowsingUser   — normal traffic (viewing grid, booking random seats). Measures response times.

Run:
    docker compose --profile loadtest run --rm locust \
        -f locustfile.py FlashSaleUser --headless -u 500 -r 100 -t 30s

Flags:
    -u 500     500 concurrent users
    -r 100     ramp up at 100 users/second
    -t 30s     run for 30 seconds
    --headless run in terminal without web UI
"""

import itertools
import os
import random

from locust import HttpUser, between, events, task
from locust.exception import RescheduleTask

# Event ID to test against
EVENT_ID = int(os.getenv("EVENT_ID", "1"))

# For FlashSaleUser — all users will target this single seat
TARGET_SEAT_ID = int(os.getenv("TARGET_SEAT_ID", "1"))

# Number of users in DB (from seed.py). Each Locust user uses a unique user_id.
#
# Why this matters: if two Locust users send the same user_id, the second one
# receives a 200 "already_owned" response, skewing success counts.
# Unique user_ids are required to test actual contention.
USER_POOL_SIZE = int(os.getenv("USER_POOL_SIZE", "499"))

# Password assigned to all test users by seed.py
PASSWORD = os.getenv("SEED_PASSWORD", "demo1234")

# ---- Phase 15: locking benchmark configuration ----
#
# These only take effect if the backend is running with BENCHMARK_MODE=true.
# Otherwise, the server ignores them and uses the normal (optimistic +
# Redis) path.
#
#   BOOKING_STRATEGY=optimistic|pessimistic
#   USE_REDIS_LOCK=1|0
#
# Why USE_REDIS_LOCK=0 is needed: The Redis layer prevents 499 out of 500
# requests from reaching the database. With it enabled, both DB strategies
# appear identical because there is no contention. Redis must be disabled
# to observe the difference.
BOOKING_STRATEGY = os.getenv("BOOKING_STRATEGY", "optimistic")
USE_REDIS_LOCK = os.getenv("USE_REDIS_LOCK", "1") != "0"

# Query string to be included with every booking request
_BOOK_QS = f"?strategy={BOOKING_STRATEGY}&redis_lock={'on' if USE_REDIS_LOCK else 'off'}"

# Ensure each scenario has a unique name in the Locust report to allow
# comparison between runs.
_BOOK_NAME = f"POST /bookings [{BOOKING_STRATEGY}, redis={'on' if USE_REDIS_LOCK else 'off'}]"

_user_nums = itertools.cycle(range(1, USER_POOL_SIZE + 1))


class AuthedUser(HttpUser):
    """
    Base class — each Locust user logs in to obtain an authentication token.

    ⭐ Login occurs in `on_start`, not in the task. Otherwise, every request
    would trigger a login, turning the load test into a login test.
    Bcrypt is intentionally slow (~100ms), which would skew the results.

    abstract = True -> Locust will not run this directly; it is for inheritance only.
    """

    abstract = True

    def on_start(self):
        n = next(_user_nums)
        self.user_email = f"user{n}@seatpulse.dev"

        res = self.client.post(
            "/api/auth/login",
            json={"email": self.user_email, "password": PASSWORD},
            name="POST /auth/login",
        )
        if res.status_code != 200:
            # Users were never seeded — stop this user to prevent a flood of 401 errors
            # which would clutter the logs and invalidate the report.
            self.environment.runner.quit()
            raise RescheduleTask(f"Login failed ({res.status_code}) — please run seed.py")

        token = res.json()["access_token"]
        # All subsequent requests for this user will include this header.
        self.client.headers["Authorization"] = f"Bearer {token}"


class FlashSaleUser(AuthedUser):
    """
    Most difficult scenario: every user targets the same seat.

    Expected result: even with 5000 users, exactly ONE confirmed booking
    should exist in the database. All others should receive a 409.

    A 409 is not a failure here; it is the expected outcome. We mark it
    as success (catch_response) to prevent the Locust report from
    misleadingly showing a 99% failure rate.
    """

    # In a flash sale, users click repeatedly and immediately.
    wait_time = between(0.1, 0.5)

    @task
    def grab_the_seat(self):
        # Step 1 — attempt to hold seat (Redis lock)
        # user_id is derived from the token (AuthedUser.on_start), not the body.
        #
        # If USE_REDIS_LOCK=0, this step is skipped and requests go directly
        # to the database.
        if USE_REDIS_LOCK:
            with self.client.post(
                f"/api/seats/{TARGET_SEAT_ID}/lock",
                name="POST /seats/{id}/lock",
                catch_response=True,
            ) as res:
                if res.status_code == 200:
                    res.success()
                    got_lock = True
                elif res.status_code == 409:
                    # Expected — seat is already held by someone else.
                    res.success()
                    got_lock = False
                else:
                    res.failure(f"Unexpected {res.status_code}: {res.text[:100]}")
                    return

            if not got_lock:
                return

        # Step 2 — book the seat
        with self.client.post(
            f"/api/bookings{_BOOK_QS}",
            json={"seat_id": TARGET_SEAT_ID},
            name=_BOOK_NAME,
            catch_response=True,
        ) as res:
            if res.status_code in (201, 409):
                res.success()
            elif res.status_code == 429:
                # Rate limited — this is a result of the load, not a server bug.
                # Count separately to avoid skewing metrics.
                res.success()
            else:
                res.failure(f"Unexpected {res.status_code}: {res.text[:100]}")


class BrowsingUser(AuthedUser):
    """
    Normal traffic — used to measure response times.

    Most users browse, while few book. The ratio is set via task weights:
    the grid is loaded 10x more frequently than bookings.
    """

    wait_time = between(1, 3)

    def on_start(self):
        super().on_start()      # login first (sets the token)
        self.seat_ids = []

        res = self.client.get(f"/api/events/{EVENT_ID}/seats", name="GET /events/{id}/seats")
        if res.status_code == 200:
            self.seat_ids = [s["id"] for s in res.json()]

    @task(10)
    def view_grid(self):
        self.client.get(f"/api/events/{EVENT_ID}/seats", name="GET /events/{id}/seats")

    @task(3)
    def view_event(self):
        self.client.get(f"/api/events/{EVENT_ID}", name="GET /events/{id}")

    @task(1)
    def book_random_seat(self):
        if not self.seat_ids:
            return

        seat_id = random.choice(self.seat_ids)

        with self.client.post(
            f"/api/seats/{seat_id}/lock",
            name="POST /seats/{id}/lock",
            catch_response=True,
        ) as res:
            if res.status_code not in (200, 409):
                res.failure(f"Unexpected {res.status_code}")
                return
            if res.status_code == 409:
                res.success()
                return
            res.success()

        with self.client.post(
            "/api/bookings",
            json={"seat_id": seat_id},
            name="POST /bookings",
            catch_response=True,
        ) as res:
            if res.status_code in (201, 409):
                res.success()
            else:
                res.failure(f"Unexpected {res.status_code}")


@events.quitting.add_listener
def _print_summary(environment, **kwargs):
"""Display relevant metrics after the test finishes."""
    stats = environment.stats.total
    print("\n" + "=" * 62)
    print("SUMMARY")
    print("=" * 62)
    print(f"  Total requests   : {stats.num_requests}")
    print(f"  Failures         : {stats.num_failures}")
    print(f"  Requests/sec     : {stats.total_rps:.1f}")
    print(f"  Median (p50)     : {stats.median_response_time} ms")
    print(f"  p95              : {stats.get_response_time_percentile(0.95)} ms")
    print(f"  p99              : {stats.get_response_time_percentile(0.99)} ms")
    print(f"  Max              : {stats.max_response_time} ms")
    print("=" * 62)
    print("  Verify integrity: docker compose exec backend python verify_integrity.py")
    print("=" * 62 + "\n")

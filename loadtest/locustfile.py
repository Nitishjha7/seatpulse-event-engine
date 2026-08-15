"""
Load tests — daawe ko number me badalne ke liye.

Do scenarios:

  FlashSaleUser  — sab ek hi seat pe toot pade. Ye "overselling nahi hoti"
                   wala claim prove karta hai.
  BrowsingUser   — normal traffic (grid dekhna, alag-alag seats book karna).
                   Ye response times measure karne ke liye hai.

Chalao:
    docker compose --profile loadtest run --rm locust \
        -f locustfile.py FlashSaleUser --headless -u 500 -r 100 -t 30s

Flags:
    -u 500     500 concurrent users
    -r 100     100 users/second ki speed se badhao
    -t 30s     30 second chalao
    --headless web UI ke bina, seedha terminal me
"""

import itertools
import os
import random

from locust import HttpUser, between, events, task

# Kis event pe test karna hai
EVENT_ID = int(os.getenv("EVENT_ID", "1"))

# FlashSaleUser ke liye — sab isi ek seat ke peeche padenge
TARGET_SEAT_ID = int(os.getenv("TARGET_SEAT_ID", "1"))

# DB me kitne users hain (seed.py se). Har Locust user ek alag user_id lega.
#
# Ye zaroori kyu: agar do Locust users same user_id bhejein, to dusre ko
# "already_owned" wala 200 mil jayega aur success count galat ho jayega.
# Alag-alag user_id se hi asli contention test hoti hai.
USER_POOL_SIZE = int(os.getenv("USER_POOL_SIZE", "500"))

_user_ids = itertools.cycle(range(1, USER_POOL_SIZE + 1))


class FlashSaleUser(HttpUser):
    """
    Sabse kathin scenario: har user EK hi seat chahta hai.

    Expected result: chahe 5000 users hon, database me exactly EK
    confirmed booking honi chahiye. Baaki sabko 409 milna chahiye.

    409 yahan FAILURE nahi hai — wahi to sahi jawab hai. Isliye humne
    unhe success maana hai (catch_response), warna Locust ka report
    "99% failure" dikhata jo galatfehmi paida karta.
    """

    # Flash sale me koi soch ke click nahi karta — turant, bar bar
    wait_time = between(0.1, 0.5)

    def on_start(self):
        self.user_id = next(_user_ids)

    @task
    def grab_the_seat(self):
        # Step 1 — seat hold karne ki koshish (Redis lock)
        with self.client.post(
            f"/api/seats/{TARGET_SEAT_ID}/lock",
            json={"user_id": self.user_id},
            name="POST /seats/{id}/lock",
            catch_response=True,
        ) as res:
            if res.status_code == 200:
                res.success()
                got_lock = True
            elif res.status_code == 409:
                # Expected — seat kisi aur ke paas hai
                res.success()
                got_lock = False
            else:
                res.failure(f"Unexpected {res.status_code}: {res.text[:100]}")
                return

        if not got_lock:
            return

        # Step 2 — lock mila to turant book karo
        with self.client.post(
            "/api/bookings",
            json={"seat_id": TARGET_SEAT_ID, "user_id": self.user_id},
            name="POST /bookings",
            catch_response=True,
        ) as res:
            if res.status_code in (201, 409):
                res.success()
            else:
                res.failure(f"Unexpected {res.status_code}: {res.text[:100]}")


class BrowsingUser(HttpUser):
    """
    Normal traffic — response times measure karne ke liye.

    Zyadatar log sirf dekhte hain, kuch hi book karte hain. Ratio task ke
    weight se set kiya hai: grid 10x zyada load hota hai booking se.
    """

    wait_time = between(1, 3)

    def on_start(self):
        self.user_id = next(_user_ids)
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
            json={"user_id": self.user_id},
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
            json={"seat_id": seat_id, "user_id": self.user_id},
            name="POST /bookings",
            catch_response=True,
        ) as res:
            if res.status_code in (201, 409):
                res.success()
            else:
                res.failure(f"Unexpected {res.status_code}")


@events.quitting.add_listener
def _print_summary(environment, **kwargs):
    """Test khatam hone par kaam ke numbers dikhao."""
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
    print("  Ab verify karo: docker compose exec backend python verify_integrity.py")
    print("=" * 62 + "\n")

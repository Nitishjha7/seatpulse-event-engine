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
from locust.exception import RescheduleTask

# Kis event pe test karna hai
EVENT_ID = int(os.getenv("EVENT_ID", "1"))

# FlashSaleUser ke liye — sab isi ek seat ke peeche padenge
TARGET_SEAT_ID = int(os.getenv("TARGET_SEAT_ID", "1"))

# DB me kitne users hain (seed.py se). Har Locust user ek alag user_id lega.
#
# Ye zaroori kyu: agar do Locust users same user_id bhejein, to dusre ko
# "already_owned" wala 200 mil jayega aur success count galat ho jayega.
# Alag-alag user_id se hi asli contention test hoti hai.
USER_POOL_SIZE = int(os.getenv("USER_POOL_SIZE", "499"))

# seed.py sab test users ko yahi password deta hai
PASSWORD = os.getenv("SEED_PASSWORD", "demo1234")

# ---- Phase 15: locking benchmark ke knobs ----
#
# Ye tabhi asar karte hain jab backend BENCHMARK_MODE=true se chal raha ho.
# Warna server inhe chupchaap ignore kar deta hai aur normal (optimistic +
# Redis) path chalta hai.
#
#   BOOKING_STRATEGY=optimistic|pessimistic
#   USE_REDIS_LOCK=1|0
#
# USE_REDIS_LOCK=0 kyu chahiye: Redis layer 500 me se 499 requests ko
# database tak pahunchne hi nahi deti. Uske rehte dono DB strategies
# bilkul ek jaisi dikhti hain — kyunki unpe contention aata hi nahi.
# Farak dekhne ke liye Redis hatana padta hai.
BOOKING_STRATEGY = os.getenv("BOOKING_STRATEGY", "optimistic")
USE_REDIS_LOCK = os.getenv("USE_REDIS_LOCK", "1") != "0"

# Query string jo har booking request ke saath jayegi
_BOOK_QS = f"?strategy={BOOKING_STRATEGY}&redis_lock={'on' if USE_REDIS_LOCK else 'off'}"

# Locust ki report me har scenario alag naam se dikhe, warna chaar runs
# ka comparison karna namumkin ho jata
_BOOK_NAME = f"POST /bookings [{BOOKING_STRATEGY}, redis={'on' if USE_REDIS_LOCK else 'off'}]"

_user_nums = itertools.cycle(range(1, USER_POOL_SIZE + 1))


class AuthedUser(HttpUser):
    """
    Base class — har Locust user apna account login karke token le leta hai.

    ⭐ Login `on_start` me hota hai, task me nahi. Warna har request se
    pehle ek login bhi jata aur load test asal me login ka test ban jata.
    Bcrypt jaan-boojh ke slow hai (~100ms), wo numbers kharab kar deta.

    abstract = True -> Locust isko khud se run nahi karega, sirf inherit
    karne ke liye hai.
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
            # Users seed nahi hue — is user ko rok do, warna 401 ka
            # dher lag jayega aur report bekaar ho jayegi
            self.environment.runner.quit()
            raise RescheduleTask(f"Login fail ({res.status_code}) — seed.py chalao")

        token = res.json()["access_token"]
        # Ab se is user ke saare requests me ye header apne aap jayega
        self.client.headers["Authorization"] = f"Bearer {token}"


class FlashSaleUser(AuthedUser):
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

    @task
    def grab_the_seat(self):
        # Step 1 — seat hold karne ki koshish (Redis lock)
        # user_id body me nahi jata — token se aata hai (AuthedUser.on_start)
        #
        # Benchmark me USE_REDIS_LOCK=0 ho to ye step poora skip hota hai
        # aur har request seedha database pe jaati hai.
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
                    # Expected — seat kisi aur ke paas hai
                    res.success()
                    got_lock = False
                else:
                    res.failure(f"Unexpected {res.status_code}: {res.text[:100]}")
                    return

            if not got_lock:
                return

        # Step 2 — book karo
        with self.client.post(
            f"/api/bookings{_BOOK_QS}",
            json={"seat_id": TARGET_SEAT_ID},
            name=_BOOK_NAME,
            catch_response=True,
        ) as res:
            if res.status_code in (201, 409):
                res.success()
            elif res.status_code == 429:
                # Rate limiter ne roka — ye load ka natija hai, server ka
                # bug nahi. Alag se ginte hain taki numbers me na ghule.
                res.success()
            else:
                res.failure(f"Unexpected {res.status_code}: {res.text[:100]}")


class BrowsingUser(AuthedUser):
    """
    Normal traffic — response times measure karne ke liye.

    Zyadatar log sirf dekhte hain, kuch hi book karte hain. Ratio task ke
    weight se set kiya hai: grid 10x zyada load hota hai booking se.
    """

    wait_time = between(1, 3)

    def on_start(self):
        super().on_start()      # pehle login (token set hota hai)
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

"""
Capture README screenshots with Playwright.

Runs inside the compose network: the page is served from the frontend
container's IP and its API calls go to `backend:8000`.

⚠️ Navigation happens by CLICKING links, never `page.goto()`.

The access token lives in a module variable (never localStorage), so a full
page reload loses it — and the refresh cookie is first-party to the API
origin, which does not apply here. Clicking keeps the SPA alive.
"""

import os
import pathlib

import httpx
from playwright.sync_api import sync_playwright

WEB = os.getenv("WEB_URL", "http://frontend:5173")
API = os.getenv("API_URL", "http://backend:8000")
OUT = pathlib.Path("/out")
VIEWPORT = {"width": 1440, "height": 980}


def token_for(email):
    r = httpx.post(f"{API}/api/auth/login",
                   json={"email": email, "password": "demo1234"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def clear_rate_limits():
    """Seeding makes many booking calls in a row; the buckets must be reset."""
    import redis
    try:
        r = redis.Redis(host="redis", port=6379, decode_responses=True)
        for key in r.scan_iter("rl:*", count=500):
            r.delete(key)
    except Exception as e:
        print("  rate limit clear skipped:", type(e).__name__)


def seed_state():
    """Create a realistic mix of seat states for the grid screenshot."""
    demo = token_for("demo@seatpulse.dev")
    H = {"Authorization": f"Bearer {demo}"}

    with httpx.Client(base_url=API, timeout=30) as c:
        free = [s["id"] for s in c.get("/api/events/1/seats").json()
                if s["status"] == "available"]

        for sid in free[:8]:
            c.post("/api/bookings", json={"seat_id": sid}, headers=H)
            clear_rate_limits()

        free = [s["id"] for s in c.get("/api/events/1/seats").json()
                if s["status"] == "available"]
        g = c.post("/api/groups",
                   json={"seat_ids": free[12:15], "deadline_minutes": 30}, headers=H)
        if g.status_code != 201:
            print("  group create failed:", g.status_code, g.text[:120])
            return None
        clear_rate_limits()

        body = g.json()
        token, shares = body["share_token"], body["shares"]

        other = token_for("user1@seatpulse.dev")
        c.post(f"/api/groups/{token}/shares/{shares[1]['id']}/claim",
               headers={"Authorization": f"Bearer {other}"})

        pay = c.post(f"/api/groups/{token}/shares/{shares[0]['id']}/pay", headers=H)
        if pay.status_code == 200:
            c.post(f"/api/payments/{pay.json()['payment_id']}/simulate",
                   json={"outcome": "success"}, headers=H)
        clear_rate_limits()
        return token


def login(page, email):
    page.goto(f"{WEB}/", wait_until="domcontentloaded")
    page.wait_for_selector('input[type="email"]', timeout=20000)
    page.fill('input[type="email"]', email)
    page.fill('input[type="password"]', "demo1234")
    page.click('button[type="submit"]')
    page.wait_for_selector('a[href="/events"]', timeout=20000)
    page.wait_for_timeout(2500)


def main():
    clear_rate_limits()
    share_token = seed_state()

    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            # Headless Chromium has no camera and no BarcodeDetector by
            # default, which would make the gate portal render its
            # "unsupported browser" fallback instead of the scanner.
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
            "--enable-blink-features=ShapeDetection",
        ])

        # ---- attendee ----
        ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
        page = ctx.new_page()
        login(page, "demo@seatpulse.dev")

        # Hold an available seat so the right rail shows a live hold
        page.evaluate("""() => {
            const b = [...document.querySelectorAll('button')]
                .find(x => x.className.includes('emerald-500/85'));
            if (b) b.click();
        }""")
        page.wait_for_timeout(2500)

        # Run an AI search so the panel shows the interpreted filters
        try:
            page.fill('input[placeholder*="2 seats together"]',
                      "3 seats together under 2000 near the stage")
            page.click('button:has-text("Search")')
            page.wait_for_timeout(7000)
        except Exception as e:
            print("  search skipped:", type(e).__name__)

        page.screenshot(path=str(OUT / "seat-grid.png"))
        print("  seat-grid.png")

        # Group page — client-side navigation via the router, so the
        # in-memory access token survives (a goto() would reload and lose it)
        if share_token:
            try:
                page.evaluate(
                    """(t) => {
                        window.history.pushState({}, '', '/groups/' + t);
                        window.dispatchEvent(new PopStateEvent('popstate'));
                    }""",
                    share_token,
                )
                page.wait_for_selector("text=Group booking", timeout=20000)
                page.wait_for_timeout(3000)
                page.screenshot(path=str(OUT / "group-booking.png"))
                print("  group-booking.png")
            except Exception as e:
                print("  group-booking skipped:", type(e).__name__, str(e)[:70])

        ctx.close()

        # ---- organizer ----
        ctx2 = browser.new_context(viewport=VIEWPORT, device_scale_factor=2,
                                   permissions=["camera"])
        page2 = ctx2.new_page()
        login(page2, "organizer@seatpulse.dev")

        page2.click('a[href="/gate"]')
        # Wait for the page itself, not the sidebar link of the same name
        page2.wait_for_selector('input[placeholder*="Token printed"]', timeout=25000)
        qr = os.getenv("QR_TOKEN", "")
        if qr:
            page2.fill('input[placeholder*="Token printed"]', qr)
            page2.click('button:has-text("Check")')
            page2.wait_for_timeout(3500)
        page2.wait_for_timeout(1500)
        page2.screenshot(path=str(OUT / "gate-checkin.png"))
        print("  gate-checkin.png")

        page2.click('a[href="/organizer/events/new"]')
        page2.wait_for_selector('input[placeholder*="Arijit Singh Live"]', timeout=25000)
        page2.wait_for_timeout(1500)
        try:
            page2.click('button:has-text("Layout builder")')
            page2.wait_for_timeout(2000)
        except Exception:
            pass
        page2.screenshot(path=str(OUT / "create-event.png"))
        print("  create-event.png")

        ctx2.close()
        browser.close()

    print("\nfiles:", sorted(f.name for f in OUT.glob("*.png")))


main()

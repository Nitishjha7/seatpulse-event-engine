"""
Group booking with split payment: all or nothing across N payments.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from helpers import auth_headers, clear_user_rate_limits


# ---------------------------------------------------------------------------
# Phase 17 — Group booking (split payment)
#
# The core question here differs from single-seat booking. There, "exactly once"
# meant: one seat, one booking. Here it means: **all or nothing**,
# across N separate payments.
# ---------------------------------------------------------------------------

@pytest.fixture
def group_seats(client, tokens):
    """3 available seats — clean up remaining ones after the test."""
    clear_user_rate_limits()

    seats = client.get("/api/events/1/seats").json()
    available = [s["id"] for s in seats if s["status"] == "available"]
    if len(available) < 3:
        pytest.skip("Not enough available seats — run reset_state.py")

    picked = available[:3]
    yield picked

    # Cleanup: remove remaining bookings. Canceling the group is not enough —
    # confirmed groups cannot be canceled.
    for token in tokens[:6]:
        for b in client.get("/api/bookings", headers=auth_headers(token)).json():
            if b["seat_id"] in picked and b["status"] == "confirmed":
                client.delete(f"/api/bookings/{b['id']}", headers=auth_headers(token))


def _make_group(client, token, seat_ids, minutes=30):
    res = client.post(
        "/api/groups",
        headers=auth_headers(token),
        json={"seat_ids": seat_ids, "deadline_minutes": minutes},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _pay_share(client, token, share_token, share_id):
    """Create a checkout for the share and simulate success via the mock provider."""
    res = client.post(
        f"/api/groups/{share_token}/shares/{share_id}/pay", headers=auth_headers(token)
    )
    assert res.status_code == 200, res.text
    pid = res.json()["payment_id"]
    return client.post(
        f"/api/payments/{pid}/simulate",
        json={"outcome": "success"},
        headers=auth_headers(token),
    )


def _seat(client, seat_id):
    return client.get(f"/api/seats/{seat_id}").json()


def test_group_holds_seats_without_booking_them(client, tokens, group_seats):
    """
    Seats are held when a group is created, NOT booked.

    This distinction is the foundation of the feature: no seat is confirmed
    until payment is received.
    """
    group = _make_group(client, tokens[0], group_seats)

    assert group["status"] == "collecting"
    assert group["total_shares"] == 3
    assert group["paid_shares"] == 0

    for seat_id in group_seats:
        assert _seat(client, seat_id)["status"] == "group_held"

    client.delete(f"/api/groups/{group['share_token']}", headers=auth_headers(tokens[0]))


def test_partial_payment_confirms_nobody(client, tokens, group_seats):
    """
    ⭐ 2 out of 3 paid — no one's seat should be booked.

    This is the real test of "all or nothing".
    """
    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]

    client.post(f"/api/groups/{st}/shares/{group['shares'][1]['id']}/claim",
                headers=auth_headers(tokens[1]))

    _pay_share(client, tokens[0], st, group["shares"][0]["id"])
    _pay_share(client, tokens[1], st, group["shares"][1]["id"])

    after = client.get(f"/api/groups/{st}", headers=auth_headers(tokens[0])).json()
    assert after["paid_shares"] == 2
    assert after["status"] == "collecting", "Should not confirm with 2 out of 3"

    # not a single seat is booked
    for seat_id in group_seats:
        assert _seat(client, seat_id)["status"] == "group_held"

    client.delete(f"/api/groups/{st}", headers=auth_headers(tokens[0]))


def test_all_paid_confirms_everyone(client, tokens, group_seats):
    """The final payment confirms everyone at once."""
    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]

    for i in (1, 2):
        client.post(f"/api/groups/{st}/shares/{group['shares'][i]['id']}/claim",
                    headers=auth_headers(tokens[i]))

    for i in (0, 1, 2):
        _pay_share(client, tokens[i], st, group["shares"][i]["id"])

    final = client.get(f"/api/groups/{st}", headers=auth_headers(tokens[0])).json()
    assert final["status"] == "confirmed"
    assert final["paid_shares"] == 3

    for seat_id in group_seats:
        assert _seat(client, seat_id)["status"] == "booked"

    # Three separate bookings for three different users, not three for one user.
    owners = set()
    for i in (0, 1, 2):
        for b in client.get("/api/bookings", headers=auth_headers(tokens[i])).json():
            if b["seat_id"] in group_seats and b["status"] == "confirmed":
                owners.add(i)
    assert owners == {0, 1, 2}


def test_expired_group_releases_seats_and_refunds(client, tokens, group_seats):
    """
    ⭐ Deadline passed — seats are released and payments are refunded.

    Shift the deadline back in the DB; a real 30-minute wait is not feasible in tests.
    """
    from datetime import timedelta

    from sqlalchemy import update as sa_update

    from database import SessionLocal
    from groups import expire_due_groups
    from models import GroupBooking, utcnow

    group = _make_group(client, tokens[0], group_seats, minutes=5)
    st = group["share_token"]

    _pay_share(client, tokens[0], st, group["shares"][0]["id"])

    db = SessionLocal()
    try:
        db.execute(
            sa_update(GroupBooking)
            .where(GroupBooking.share_token == st)
            .values(expires_at=utcnow() - timedelta(minutes=1))
        )
        db.commit()
        # Call the job directly — no need to wait for the 30-second cron.
        expire_due_groups(db)
    finally:
        db.close()

    after = client.get(f"/api/groups/{st}", headers=auth_headers(tokens[0])).json()
    assert after["status"] == "expired"

    # Refund the payer, leave others as unpaid.
    statuses = [s["status"] for s in after["shares"]]
    assert statuses.count("refunded") == 1
    assert statuses.count("unpaid") == 2

    for seat_id in group_seats:
        assert _seat(client, seat_id)["status"] == "available"


def test_pending_payment_dies_with_the_group(client, tokens, group_seats):
    """
    If the group breaks, any open checkout is invalidated.

    The user was on the gateway page when the deadline passed. The best outcome is to avoid charging them entirely — not charging is better than a refund. Therefore, `break_group` expires pending payments.
    """
    from datetime import timedelta

    from sqlalchemy import update as sa_update

    from database import SessionLocal
    from groups import expire_due_groups
    from models import GroupBooking, GroupShare, Payment, utcnow

    group = _make_group(client, tokens[0], group_seats, minutes=5)
    st = group["share_token"]
    share_id = group["shares"][0]["id"]

    res = client.post(f"/api/groups/{st}/shares/{share_id}/pay",
                      headers=auth_headers(tokens[0]))
    assert res.status_code == 200
    payment_id = res.json()["payment_id"]

    db = SessionLocal()
    try:
        db.execute(
            sa_update(GroupBooking)
            .where(GroupBooking.share_token == st)
            .values(expires_at=utcnow() - timedelta(minutes=1))
        )
        db.commit()
        expire_due_groups(db)

        assert db.get(Payment, payment_id).status == "expired"
        share = db.get(GroupShare, share_id)
        assert share.status == "unpaid", "Should not be 'paid' if no money was charged."
        assert share.booking_id is None
    finally:
        db.close()

    assert _seat(client, group_seats[0])["status"] == "available"


def test_late_webhook_after_expiry_is_refunded_not_booked(client, tokens, group_seats):
    """
    ⭐⭐ The most difficult case: the group has expired, but the gateway reports "payment received".

    The previous test shows we close the checkout. However, the real gateway does not stop when we do — webhooks can arrive late, after the payment has already been processed.

    In that situation, the seat cannot be reclaimed (it was released and perhaps taken by someone else). The only correct response is a **refund**.

    We call `_fulfil` directly here because the `/simulate` endpoint does not handle expired payments, whereas a real webhook would.
    """
    from datetime import timedelta

    from sqlalchemy import update as sa_update

    from database import SessionLocal
    from groups import expire_due_groups
    from models import GroupBooking, GroupShare, Payment, utcnow
    from routers.payments import _fulfil

    group = _make_group(client, tokens[0], group_seats, minutes=5)
    st = group["share_token"]
    share_id = group["shares"][0]["id"]

    res = client.post(f"/api/groups/{st}/shares/{share_id}/pay",
                      headers=auth_headers(tokens[0]))
    payment_id = res.json()["payment_id"]

    db = SessionLocal()
    try:
        db.execute(
            sa_update(GroupBooking)
            .where(GroupBooking.share_token == st)
            .values(expires_at=utcnow() - timedelta(minutes=1))
        )
        db.commit()
        expire_due_groups(db)

        # Late "succeeded" notification from the gateway.
        _fulfil(db, db.get(Payment, payment_id))

        share = db.get(GroupShare, share_id)
        assert share.status == "refunded",             "Late payments must be refunded."
        assert share.booking_id is None, "No booking should be created for an expired group."
        assert db.get(Payment, payment_id).status == "refunded"
    finally:
        db.close()

    assert _seat(client, group_seats[0])["status"] == "available"


def test_only_one_person_can_claim_a_share(client, tokens, group_seats):
    """Two people claim the same open seat at once — only one may win."""
    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]
    open_share = group["shares"][1]["id"]

    def claim(token):
        return client.post(
            f"/api/groups/{st}/shares/{open_share}/claim", headers=auth_headers(token)
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(claim, [tokens[1], tokens[2]]))

    assert codes.count(200) == 1, f"Exactly one claim expected: {codes}"
    assert codes.count(409) == 1

    client.delete(f"/api/groups/{st}", headers=auth_headers(tokens[0]))


def test_cannot_pay_someone_elses_share(client, tokens, group_seats):
    """You cannot pay for a share you did not claim."""
    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]

    # share[0] belongs to the creator (tokens[0])
    res = client.post(
        f"/api/groups/{st}/shares/{group['shares'][0]['id']}/pay",
        headers=auth_headers(tokens[1]),
    )
    assert res.status_code == 403

    client.delete(f"/api/groups/{st}", headers=auth_headers(tokens[0]))


def test_group_creation_is_all_or_nothing(client, tokens, group_seats):
    """
    ⭐ If even one seat is unavailable, the ENTIRE group should fail.

    Partial holds are useless — a user shouldn't be left waiting for a 3rd seat that will never be available.
    """
    # Book one seat
    taken = group_seats[2]
    assert client.post("/api/bookings", json={"seat_id": taken},
                       headers=auth_headers(tokens[5])).status_code == 201

    res = client.post(
        "/api/groups",
        headers=auth_headers(tokens[0]),
        json={"seat_ids": group_seats},
    )
    assert res.status_code == 409

    # ⭐ The remaining two seats should be RELEASED — not stuck in group_held.
    for seat_id in group_seats[:2]:
        assert _seat(client, seat_id)["status"] == "available", \
            "Failed group creation left seats in hold."


def test_unknown_share_token_is_404(client, tokens):
    """Guessing a token must not grant access to someone else's group."""
    res = client.get("/api/groups/definitely-not-a-real-token",
                     headers=auth_headers(tokens[0]))
    assert res.status_code == 404


def test_only_creator_can_cancel(client, tokens, group_seats):
    """A non-creator gets 404, not 403 — existence stays hidden."""
    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]

    assert client.delete(f"/api/groups/{st}",
                         headers=auth_headers(tokens[1])).status_code == 404
    assert client.delete(f"/api/groups/{st}",
                         headers=auth_headers(tokens[0])).status_code == 200


def test_confirm_and_expiry_race_has_exactly_one_winner(client, tokens, group_seats):
    """
    ⭐⭐ The most difficult test in Phase 17.

    The last person is paying while the expiry job is breaking the group. Exactly one must win, with proper cleanup for the loser:

      confirm wins -> all seats booked, all bookings created
      expire wins  -> all seats available, payments refunded

    Never a partial state: no group stuck in 'collecting', no paid share without a booking.

    This race condition was broken without `FOR UPDATE` — the payment thread would read the group status, the expiry job would expire it, and the share would remain 'paid' without a refund.
    """
    import random
    import threading
    import time
    from datetime import timedelta

    from sqlalchemy import select as sa_select, update as sa_update

    from database import SessionLocal
    from groups import expire_due_groups
    from models import GroupBooking, GroupShare, utcnow

    group = _make_group(client, tokens[0], group_seats[:2], minutes=5)
    st = group["share_token"]

    client.post(f"/api/groups/{st}/shares/{group['shares'][1]['id']}/claim",
                headers=auth_headers(tokens[1]))

    _pay_share(client, tokens[0], st, group["shares"][0]["id"])

    # Checkout for the final share created, settlement pending.
    res = client.post(f"/api/groups/{st}/shares/{group['shares'][1]['id']}/pay",
                      headers=auth_headers(tokens[1]))
    assert res.status_code == 200
    payment_id = res.json()["payment_id"]

    db = SessionLocal()
    try:
        db.execute(
            sa_update(GroupBooking)
            .where(GroupBooking.share_token == st)
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        db.commit()
    finally:
        db.close()

    barrier = threading.Barrier(2)

    def settle():
        barrier.wait()
        client.post(f"/api/payments/{payment_id}/simulate",
                    json={"outcome": "success"}, headers=auth_headers(tokens[1]))

    def expire():
        barrier.wait()
        # Jitter — without this, expiry always wins (direct function call vs full HTTP stack), and the other path is never tested.
        time.sleep(random.uniform(0, 0.12))
        d = SessionLocal()
        try:
            expire_due_groups(d)
        finally:
            d.close()

    t1, t2 = threading.Thread(target=settle), threading.Thread(target=expire)
    t1.start(); t2.start(); t1.join(); t2.join()

    db = SessionLocal()
    try:
        g = db.scalar(sa_select(GroupBooking).where(GroupBooking.share_token == st))
        shares = db.scalars(
            sa_select(GroupShare).where(GroupShare.group_id == g.id)
        ).all()

        assert g.status in ("confirmed", "expired"), \
            f"Group stuck in '{g.status}' — no winner."

        seat_states = [_seat(client, s.seat_id)["status"] for s in shares]

        if g.status == "confirmed":
            assert all(x == "booked" for x in seat_states), seat_states
            assert all(s.booking_id is not None for s in shares)
        else:
            assert all(x == "available" for x in seat_states), seat_states
            assert all(s.booking_id is None for s in shares)
            # ⭐ Payments already received must be refunded.
            for s in shares:
                assert s.status in ("refunded", "unpaid"), \
                    f"Share '{s.status}' in expired group — payment is stuck."
    finally:
        db.close()


def test_broken_group_does_not_leave_pending_payments(client, tokens, group_seats):
    """
    If a group is cancelled, its PENDING payments must also be closed.

    Otherwise, two issues arise:
      1. `uq_one_pending_payment_per_seat` prevents new checkouts for that seat — it appears 'available' but cannot be purchased.
      2. A user could complete an old checkout and pay for a defunct group.

    This was a real bug discovered while writing race condition tests.
    """
    from sqlalchemy import select as sa_select

    from database import SessionLocal
    from models import Payment

    group = _make_group(client, tokens[0], group_seats)
    st = group["share_token"]

    res = client.post(f"/api/groups/{st}/shares/{group['shares'][0]['id']}/pay",
                      headers=auth_headers(tokens[0]))
    assert res.status_code == 200

    client.delete(f"/api/groups/{st}", headers=auth_headers(tokens[0]))

    db = SessionLocal()
    try:
        still_pending = db.scalars(
            sa_select(Payment).where(
                Payment.seat_id.in_(group_seats), Payment.status == "pending"
            )
        ).all()
        assert not still_pending, f"{len(still_pending)} pending payments remain stuck"
    finally:
        db.close()

    # Now the same seat can be purchased normally — this is the actual check.
    # Previously, this returned 409 because the old pending payment index blocked it.
    res = client.post("/api/payments/checkout",
                      json={"seat_id": group_seats[0]}, headers=auth_headers(tokens[3]))
    assert res.status_code == 201, res.text

    # Do not leave pending payments behind — otherwise, the next test will collide with this index. (The same error we are currently testing.)
    client.post(f"/api/payments/{res.json()['payment_id']}/simulate",
                json={"outcome": "fail"}, headers=auth_headers(tokens[3]))

"""
Sellout projection based on an event's own booking velocity.

This is deliberately not a machine-learned "AI forecast" — there's no
cross-event training data to learn from, and a model dressed up on top of
one event's booking history would just be guessing with extra steps. What
IS real is the event's own timeline: every confirmed booking has a
timestamp, so a plain linear extrapolation of "bookings per day so far"
is honest, explainable, and gets meaningfully more accurate as an event
collects more data — unlike a fake number that looks the same on day one
and day thirty.

Two projections come out of the same rate:
  - if the pace would sell out before the event date, project WHEN
  - otherwise, project what % will be sold BY the event date

Both are just `remaining / rate` and `rate * days_left`, respectively.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

# Below this many confirmed bookings, a rate is mostly noise — two ticket
# sales an hour apart "projects" wildly different sellout dates depending
# on which two you happen to have.
MIN_BOOKINGS = 5

# Recent activity is a better predictor than the all-time average once an
# event has been live a while — a launch-day spike shouldn't set the pace
# for the rest of the sale. Falls back to the all-time rate if the event
# is too new for a "recent" window to mean anything.
RECENT_WINDOW = timedelta(days=2)


@dataclass(frozen=True)
class Forecast:
    bookings_analyzed: int
    rate_per_day: float
    sellout_date: datetime | None            # set when sellout is projected before the event
    projected_percent_sold: float | None     # set otherwise — projection as of the event date


def estimate_sellout(
    booking_times: list[datetime],
    *,
    total_seats: int,
    booked_seats: int,
    event_starts_at: datetime,
    now: datetime,
) -> Forecast | None:
    """
    Project a sellout date or an expected sold-percentage, from a list of
    confirmed booking timestamps (any order).

    Returns None when there isn't enough of a track record yet to say
    anything better than a guess — too few bookings, or a rate of zero.
    """
    if len(booking_times) < MIN_BOOKINGS or total_seats <= 0:
        return None

    ordered = sorted(booking_times)
    first_booking = ordered[0]

    recent = [t for t in ordered if now - t <= RECENT_WINDOW]
    if len(recent) >= MIN_BOOKINGS:
        window_start = max(first_booking, now - RECENT_WINDOW)
        rate = _rate_per_day(len(recent), window_start, now)
    else:
        rate = _rate_per_day(len(ordered), first_booking, now)

    if rate <= 0:
        return None

    remaining = max(total_seats - booked_seats, 0)
    if remaining == 0:
        return Forecast(len(ordered), rate, sellout_date=now, projected_percent_sold=100.0)

    days_to_sellout = remaining / rate
    sellout_date = now + timedelta(days=days_to_sellout)

    if sellout_date <= event_starts_at:
        return Forecast(len(ordered), rate, sellout_date=sellout_date, projected_percent_sold=None)

    days_until_event = max((event_starts_at - now).total_seconds() / 86400, 0)
    projected_sold = min(booked_seats + rate * days_until_event, total_seats)
    percent = round(projected_sold / total_seats * 100, 1)
    return Forecast(len(ordered), rate, sellout_date=None, projected_percent_sold=percent)


def _rate_per_day(count: int, window_start: datetime, now: datetime) -> float:
    elapsed_days = max((now - window_start).total_seconds() / 86400, 1 / 24)
    return count / elapsed_days

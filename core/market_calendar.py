"""
Gold (OANDA XAU_USD) market calendar in New-York time — DST-correct.

OANDA's XAU_USD follows the New York trading day:
  • weekly close   Friday 17:00 ET  →  weekly open Sunday 18:00 ET
  • daily settlement break 17:00 → 18:00 ET (Mon–Thu)

Until 2026-10-08 every bot and service hard-coded this in FIXED UTC
(21:00-22:00 break, Fri 22:00 → Sun 22:00 weekend), which is only right while
the US is on daylight time. From the first Sunday of November (2026-11-01) the
real break moves to 22:00-23:00 UTC and the weekend to Fri 22:00 → Sun 23:00;
every gate would have been one hour off until March. Everything now asks this
module instead of doing hour arithmetic in UTC.

Accepts aware or naive datetimes (naive = UTC) and pandas Timestamps.
"""
from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

WEEKLY_CLOSE_WD, WEEKLY_CLOSE_H = 4, 17   # Friday 17:00 ET
WEEKLY_OPEN_WD, WEEKLY_OPEN_H = 6, 18     # Sunday 18:00 ET
BREAK_START_H, BREAK_END_H = 17, 18       # daily settlement 17:00-18:00 ET


def _to_utc(value: Any) -> dt.datetime:
    """datetime (naive = UTC) or pandas Timestamp → aware UTC datetime."""
    if value is None:
        return dt.datetime.now(UTC)
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def gold_market_state(now: Any = None) -> dict[str, Any]:
    """Full state: open/weekend/settlement flags, next open, last open, and the
    seconds since the current session opened (None while closed)."""
    now_utc = _to_utc(now)
    ny = now_utc.astimezone(NY)
    wd, h = ny.weekday(), ny.hour

    weekend = (
        (wd == WEEKLY_CLOSE_WD and h >= WEEKLY_CLOSE_H)
        or wd == 5
        or (wd == WEEKLY_OPEN_WD and h < WEEKLY_OPEN_H)
    )
    settlement = (not weekend) and BREAK_START_H <= h < BREAK_END_H
    is_open = not (weekend or settlement)

    next_open: dt.datetime | None
    if weekend:
        days = (WEEKLY_OPEN_WD - wd) % 7
        cand = (ny + dt.timedelta(days=days)).replace(
            hour=WEEKLY_OPEN_H, minute=0, second=0, microsecond=0
        )
        if cand <= ny:
            cand += dt.timedelta(days=7)
        next_open = cand
    elif settlement:
        next_open = ny.replace(hour=BREAK_END_H, minute=0, second=0, microsecond=0)
    else:
        next_open = None

    # The most recent 18:00 ET session start at or before now (only while open —
    # while open that boundary can never land on Friday/Saturday).
    last_open: dt.datetime | None = None
    if is_open:
        cand = ny.replace(hour=BREAK_END_H, minute=0, second=0, microsecond=0)
        if cand > ny:
            cand -= dt.timedelta(days=1)
        last_open = cand

    seconds_since_open = (
        (now_utc - last_open.astimezone(UTC)).total_seconds() if last_open else None
    )
    if weekend:
        reason = "Weekend — gold closed (Fri 17:00 → Sun 18:00 New York)"
    elif settlement:
        reason = "Daily settlement break (17:00 → 18:00 New York)"
    else:
        reason = None

    return {
        "open": is_open,
        "closed": not is_open,
        "weekend": weekend,
        "settlement": settlement,
        "reason": reason,
        "next_open_utc": next_open.astimezone(UTC) if next_open else None,
        "last_open_utc": last_open.astimezone(UTC) if last_open else None,
        "seconds_since_open": seconds_since_open,
        "ny_time": ny.strftime("%a %H:%M %Z"),
    }


def is_gold_open(now: Any = None) -> bool:
    return gold_market_state(now)["open"]


def gold_open_hours_between(start: Any, end: Any) -> float:
    """Open hours between two instants, hour slot by hour slot (DST-aware)."""
    s, e = _to_utc(start), _to_utc(end)
    if e <= s:
        return 0.0
    hours = 0.0
    t = s
    while t < e:
        slot_end = min(t.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1), e)
        if is_gold_open(t):
            hours += (slot_end - t).total_seconds() / 3600.0
        t = slot_end
    return hours

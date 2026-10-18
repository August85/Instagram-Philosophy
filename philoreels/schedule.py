"""When to post: the 2-a-day to 10-a-day ramp and the daily time slots.

Everything here is a pure function of dates and config so it is easy to test.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from typing import Any


def posting_week(start: date, day: date) -> int:
    """1 for the first 7 days after `start`, 2 for the next 7, and so on."""
    return (day - start).days // 7 + 1


def per_day(ramp: list[dict[str, Any]], start: date | None, day: date, cap: int | None = None) -> int:
    if start is None or day < start:
        return 0
    week = posting_week(start, day)
    n = 0
    for row in sorted(ramp, key=lambda r: r["week"]):
        if week >= row["week"]:
            n = int(row["per_day"])
    if cap is not None:
        n = min(n, cap)
    return n


def _parse_hm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def slots(day: date, n: int, window_start: str, window_end: str, jitter_minutes: int = 0) -> list[datetime]:
    """n evenly spaced posting times inside the window, each nudged by a
    deterministic jitter so the pattern differs from day to day."""
    if n <= 0:
        return []
    start = datetime.combine(day, _parse_hm(window_start))
    end = datetime.combine(day, _parse_hm(window_end))
    if end <= start:
        end += timedelta(days=1)
    span = (end - start) / n
    out = []
    for i in range(n):
        base = start + span * (i + 0.5)
        if jitter_minutes:
            h = int(hashlib.sha1(f"{day.isoformat()}-{n}-{i}".encode()).hexdigest(), 16)
            offset = (h % (2 * jitter_minutes + 1)) - jitter_minutes
            base += timedelta(minutes=offset)
        out.append(base.replace(second=0, microsecond=0))
    return out


def should_post(
    now: datetime,
    todays_slots: list[datetime],
    posted_today: int,
    last_post: datetime | None,
    min_gap_minutes: int,
) -> bool:
    """Post when more slots have passed today than reels were posted, and the
    last post was long enough ago. Missed slots (machine asleep) are caught up
    one at a time, spaced by the minimum gap."""
    due = sum(1 for s in todays_slots if s <= now)
    if posted_today >= due:
        return False
    if last_post and now - last_post < timedelta(minutes=min_gap_minutes):
        return False
    return True


def next_slot(now: datetime, todays_slots: list[datetime], posted_today: int) -> datetime | None:
    remaining = todays_slots[posted_today:]
    future = [s for s in remaining if s > now]
    return future[0] if future else None

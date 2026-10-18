from datetime import date, datetime, timedelta

from philoreels import schedule

RAMP = [
    {"week": 1, "per_day": 2}, {"week": 2, "per_day": 3}, {"week": 3, "per_day": 4},
    {"week": 4, "per_day": 5}, {"week": 5, "per_day": 6}, {"week": 6, "per_day": 8},
    {"week": 7, "per_day": 10},
]
START = date(2026, 10, 20)


def test_ramp_starts_at_two_and_reaches_ten():
    assert schedule.per_day(RAMP, START, START) == 2
    assert schedule.per_day(RAMP, START, START + timedelta(days=6)) == 2
    assert schedule.per_day(RAMP, START, START + timedelta(days=7)) == 3
    assert schedule.per_day(RAMP, START, START + timedelta(days=42)) == 10
    assert schedule.per_day(RAMP, START, START + timedelta(days=400)) == 10


def test_nothing_before_start_or_without_start():
    assert schedule.per_day(RAMP, START, START - timedelta(days=1)) == 0
    assert schedule.per_day(RAMP, None, START) == 0


def test_cap_holds_volume():
    assert schedule.per_day(RAMP, START, START + timedelta(days=60), cap=4) == 4


def test_slots_are_inside_window_sorted_and_spaced():
    for n in (2, 5, 10):
        s = schedule.slots(START, n, "07:00", "23:00", jitter_minutes=12)
        assert len(s) == n
        assert s == sorted(s)
        assert all(datetime(2026, 10, 20, 6, 48) <= t <= datetime(2026, 10, 20, 23, 12) for t in s)
        gaps = [(b - a).total_seconds() / 60 for a, b in zip(s, s[1:])]
        assert all(g >= 16 * 60 / n - 25 for g in gaps)


def test_jitter_changes_by_day():
    a = schedule.slots(START, 4, "07:00", "23:00", 12)
    b = schedule.slots(START + timedelta(days=1), 4, "07:00", "23:00", 12)
    assert [t.time() for t in a] != [t.time() for t in b]


def test_should_post_when_slot_passed():
    s = [datetime(2026, 10, 20, 11, 0), datetime(2026, 10, 20, 19, 0)]
    assert not schedule.should_post(datetime(2026, 10, 20, 10, 0), s, 0, None, 40)
    assert schedule.should_post(datetime(2026, 10, 20, 11, 5), s, 0, None, 40)
    assert not schedule.should_post(datetime(2026, 10, 20, 11, 5), s, 1, None, 40)


def test_catch_up_respects_min_gap():
    s = [datetime(2026, 10, 20, 8, 0), datetime(2026, 10, 20, 9, 0), datetime(2026, 10, 20, 10, 0)]
    now = datetime(2026, 10, 20, 12, 0)
    assert not schedule.should_post(now, s, 1, now - timedelta(minutes=10), 40)
    assert schedule.should_post(now, s, 1, now - timedelta(minutes=45), 40)

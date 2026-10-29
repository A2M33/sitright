from datetime import date

from sitright.stats import DayStats, StatsTracker

DAY = date(2026, 3, 2)


def run(tracker, face, upright, start, seconds, step=0.1, day=DAY):
    t = start
    while t < start + seconds:
        tracker.observe(face, upright, t, day)
        t += step
    return t


def test_upright_share_and_summary():
    tr = StatsTracker(away_reset_seconds=120)
    t = run(tr, True, True, 0, 82)
    run(tr, True, False, t, 18)
    assert tr.today.upright_percent == 82
    assert tr.summary() == "Today: 82% upright, 0 breaks"


def test_breaks_count_only_long_absences():
    tr = StatsTracker(away_reset_seconds=120)
    t = run(tr, True, True, 0, 60)
    t = run(tr, False, False, t, 30)  # fetched a coffee: too short
    t = run(tr, True, True, t, 60)
    t = run(tr, False, False, t, 150)  # a real break
    t = run(tr, True, True, t, 10)
    t = run(tr, False, False, t, 130, step=1)
    run(tr, True, True, t, 1)
    assert tr.today.breaks == 2
    assert tr.summary().endswith("2 breaks")


def test_paused_time_is_not_counted():
    tr = StatsTracker(away_reset_seconds=120)
    t = run(tr, True, True, 0, 61)
    run(tr, True, False, t + 3600, 1)  # an hour of pause in between
    assert 61 < tr.today.seen_seconds < 63


def test_reminders_and_new_day_saves_old_one():
    saved = []
    tr = StatsTracker(120, save=lambda s: saved.append((s.day, s.breaks, dict(s.reminders))))
    tr.reminder("SLOUCH", DAY)
    tr.reminder("SLOUCH", DAY)
    t = run(tr, True, True, 0, 5)
    run(tr, True, True, t, 1, day=date(2026, 3, 3))
    assert ("2026-03-02", 0, {"SLOUCH": 2}) in saved
    assert tr.today.day == "2026-03-03" and tr.today.reminders == {}


def test_restart_continues_the_day():
    earlier = DayStats("2026-03-02", seen_seconds=600, upright_seconds=300, breaks=1)
    tr = StatsTracker(120, load=lambda day: earlier if day == "2026-03-02" else None)
    run(tr, True, True, 0, 600)
    assert tr.today.breaks == 1
    assert tr.today.upright_percent == 75


def test_damaged_file_is_tolerated():
    s = DayStats.from_dict({"seen_seconds": "lots", "breaks": 3, "reminders": [1]}, "2026-03-02")
    assert s.seen_seconds == 0 and s.breaks == 3 and s.reminders == {}


def test_not_enough_data():
    assert DayStats("2026-03-02", seen_seconds=30, upright_seconds=30).summary() == (
        "Today: not enough data yet"
    )
    assert DayStats("d", seen_seconds=100, upright_seconds=50, breaks=1).summary() == (
        "Today: 50% upright, 1 break"
    )
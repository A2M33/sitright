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


def test_nan_and_negative_numbers_in_a_day_file_are_dropped(tmp_path, monkeypatch):
    from sitright import stats

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (stats.stats_dir() / "2026-03-02.json").write_text(
        '{"seen_seconds": NaN, "upright_seconds": Infinity, "breaks": -2,'
        ' "reminders": {"SLOUCH": 1e999, "TOO_CLOSE": 2}}',
        encoding="utf-8",
    )
    s = stats.load_day("2026-03-02")
    assert (s.seen_seconds, s.upright_seconds, s.breaks) == (0, 0, 0)
    assert s.reminders == {"TOO_CLOSE": 2}
    tr = StatsTracker(120, load=stats.load_day, save=stats.save_day)
    run(tr, True, True, 0, 61)
    assert tr.summary() == "Today: 100% upright, 0 breaks"  # used to raise on every frame
    tr.flush()


def test_more_upright_than_seen_is_capped():
    s = DayStats.from_dict({"seen_seconds": 100, "upright_seconds": 400}, "2026-03-02")
    assert s.upright_percent == 100


def test_not_enough_data():
    assert DayStats("2026-03-02", seen_seconds=30, upright_seconds=30).summary() == (
        "Today: not enough data yet"
    )
    assert DayStats("d", seen_seconds=100, upright_seconds=50, breaks=1).summary() == (
        "Today: 50% upright, 1 break"
    )


def test_a_crash_while_saving_keeps_the_days_numbers(tmp_path, monkeypatch):
    from sitright import config, stats

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    stats.save_day(DayStats("2026-03-02", seen_seconds=600, upright_seconds=300))

    def boom(_src, _dst):
        raise OSError("power cut")

    with monkeypatch.context() as patch:
        patch.setattr(config.os, "replace", boom)
        stats.save_day(DayStats("2026-03-02", seen_seconds=601, upright_seconds=301))
    assert stats.load_day("2026-03-02").seen_seconds == 600

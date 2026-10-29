from sitright.monitor import Alert, Baseline, Monitor, Sample, Thresholds, eye_aspect_ratio

BASE = Baseline(nose_y=0.50, face_width=0.30, open_ear=0.30)
GOOD = Sample(ear=0.30, nose_y=0.50, face_width=0.30)
SLOUCH = Sample(ear=0.30, nose_y=0.62, face_width=0.30)
CLOSE = Sample(ear=0.30, nose_y=0.50, face_width=0.42)
SHUT = Sample(ear=0.10, nose_y=0.50, face_width=0.30)


def feed(monitor, sample, start, seconds, step=0.1):
    alerts, t = [], start
    while t < start + seconds:
        alerts += monitor.update(sample, t)
        t += step
    return alerts, t


def blink_every(monitor, period, start, seconds, step=0.1):
    """Simulate eyes closing for one frame every `period` seconds."""
    alerts, t, next_blink = [], start, start + period
    while t < start + seconds:
        sample = SHUT if t >= next_blink else GOOD
        if sample is SHUT:
            next_blink += period
        alerts += monitor.update(sample, t)
        t += step
    return alerts, t


def test_good_posture_is_quiet():
    m = Monitor(BASE)
    alerts, _ = blink_every(m, 4, 0, 120)
    assert alerts == []
    assert m.status.ok


def test_short_slouch_is_ignored():
    m = Monitor(BASE)
    alerts, _ = feed(m, SLOUCH, 0, 5)
    assert Alert.SLOUCH not in alerts
    assert m.status.slouching


def test_long_slouch_alerts_once_per_cooldown():
    m = Monitor(BASE)
    alerts, _ = feed(m, SLOUCH, 0, 60)
    assert alerts.count(Alert.SLOUCH) == 1


def test_slouch_alerts_again_after_cooldown():
    m = Monitor(BASE, Thresholds(cooldown_seconds=30))
    alerts, _ = feed(m, SLOUCH, 0, 70)
    assert alerts.count(Alert.SLOUCH) == 3  # at 8 s, 38 s, 68 s


def test_too_close():
    m = Monitor(BASE)
    alerts, _ = feed(m, CLOSE, 0, 10)
    assert Alert.TOO_CLOSE in alerts


def test_low_blink_rate_after_full_window():
    m = Monitor(BASE)
    alerts, _ = blink_every(m, 30, 0, 59)  # ~2 blinks/min, but window not full yet
    assert Alert.LOW_BLINK not in alerts
    alerts, _ = blink_every(m, 30, 59, 5)
    assert Alert.LOW_BLINK in alerts


def test_normal_blink_rate_is_fine():
    m = Monitor(BASE)
    blink_every(m, 4, 0, 90)
    assert m.status.blinks_per_minute is not None
    assert m.status.blinks_per_minute >= 14


def test_break_reminder_and_reset_when_away():
    th = Thresholds(break_after_minutes=1, min_blinks_per_minute=0)
    m = Monitor(BASE, th)
    alerts, t = feed(m, GOOD, 0, 61, step=1)
    assert Alert.TAKE_BREAK in alerts
    _, t = feed(m, None, t, 130, step=1)  # walked away for over 2 minutes
    m.update(GOOD, t)
    assert m.status.sitting_minutes == 0


def test_leaving_resets_bad_posture_timer():
    m = Monitor(BASE)
    _, t = feed(m, SLOUCH, 0, 6)
    m.update(None, t)
    alerts, _ = feed(m, SLOUCH, t + 0.1, 6)
    assert Alert.SLOUCH not in alerts


def test_eye_aspect_ratio():
    open_eye = [(0, 0), (1, 1), (2, 1), (3, 0), (2, -1), (1, -1)]
    closed_eye = [(0, 0), (1, 0.1), (2, 0.1), (3, 0), (2, -0.1), (1, -0.1)]
    assert eye_aspect_ratio(open_eye) > eye_aspect_ratio(closed_eye) * 5
    assert eye_aspect_ratio([(0, 0)] * 6) == 0.0

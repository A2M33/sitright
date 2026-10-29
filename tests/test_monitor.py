import math

from sitright.monitor import (
    Alert,
    Baseline,
    Gate,
    Monitor,
    Reminders,
    Sample,
    Thresholds,
    eye_aspect_ratio,
    head_pitch,
)

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
    alerts, _ = feed(m, SLOUCH, 0, 25)  # reaching for a drink, reading a line up close
    assert Alert.SLOUCH not in alerts
    assert m.status.slouching


def test_slouch_alerts_after_30_seconds():
    m = Monitor(BASE)
    alerts, t = feed(m, SLOUCH, 0, 29.5)
    assert Alert.SLOUCH not in alerts
    alerts, _ = feed(m, SLOUCH, t, 1)
    assert Alert.SLOUCH in alerts


def test_long_slouch_alerts_once_per_cooldown():
    m = Monitor(BASE)
    alerts, _ = feed(m, SLOUCH, 0, 60)
    assert alerts.count(Alert.SLOUCH) == 1


def test_slouch_alerts_again_after_cooldown():
    m = Monitor(BASE, Thresholds(cooldown_seconds=30, reminder_gap_seconds=0))
    alerts, _ = feed(m, SLOUCH, 0, 100)
    assert alerts.count(Alert.SLOUCH) == 3  # at 30 s, 60 s, 90 s


def test_too_close():
    m = Monitor(BASE)
    alerts, _ = feed(m, CLOSE, 0, 31)
    assert Alert.TOO_CLOSE in alerts


def test_low_blink_rate_after_full_window():
    m = Monitor(BASE, reminders=Reminders(blink=True))
    alerts, _ = blink_every(m, 30, 0, 59)  # ~2 blinks/min, but window not full yet
    assert Alert.LOW_BLINK not in alerts
    alerts, _ = blink_every(m, 30, 59, 5)
    assert Alert.LOW_BLINK in alerts


def test_blink_reminder_is_off_by_default():
    m = Monitor(BASE)
    alerts, _ = blink_every(m, 30, 0, 120)
    assert m.status.blinks_per_minute is not None  # still measured for the tooltip
    assert Alert.LOW_BLINK not in alerts


def test_switched_off_reminder_stays_quiet():
    m = Monitor(BASE, reminders=Reminders(slouch=False))
    alerts, _ = feed(m, SLOUCH, 0, 60)
    assert m.status.slouching
    assert Alert.SLOUCH not in alerts


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


def test_long_gap_without_frames_counts_as_a_break():
    th = Thresholds(break_after_minutes=10, min_blinks_per_minute=0)
    m = Monitor(BASE, th)
    _, t = feed(m, GOOD, 0, 9 * 60, step=1)
    # No frames at all for an hour (camera unplugged or paused), then back.
    alerts = m.update(GOOD, t + 3600)
    assert Alert.TAKE_BREAK not in alerts
    assert m.status.sitting_minutes == 0


def test_leaving_resets_bad_posture_timer():
    m = Monitor(BASE)
    _, t = feed(m, SLOUCH, 0, 20)
    _, t = feed(m, None, t, 2)  # gone for 2 s, longer than the grace period
    assert not m.status.face
    alerts, _ = feed(m, SLOUCH, t, 20)
    assert Alert.SLOUCH not in alerts


def test_one_missed_frame_keeps_the_slouch_timer():
    m = Monitor(BASE)
    _, t = feed(m, SLOUCH, 0, 20)
    m.update(None, t)  # the model lost the face for one frame
    alerts, _ = feed(m, SLOUCH, t + 0.1, 11)
    assert Alert.SLOUCH in alerts  # 20 s + 11 s of slouching passes the 30 s hold


def test_short_dropouts_keep_the_blink_window():
    m = Monitor(BASE)
    _, t = blink_every(m, 4, 0, 61)
    assert m.status.blinks_per_minute is not None
    _, t = feed(m, None, t, 1.0)  # under the 1.5 s grace period
    m.update(GOOD, t)
    assert m.status.face
    assert m.status.blinks_per_minute is not None  # not back to "measuring..."


def test_eye_aspect_ratio():
    open_eye = [(0, 0), (1, 1), (2, 1), (3, 0), (2, -1), (1, -1)]
    closed_eye = [(0, 0), (1, 0.1), (2, 0.1), (3, 0), (2, -0.1), (1, -0.1)]
    assert eye_aspect_ratio(open_eye) > eye_aspect_ratio(closed_eye) * 5
    assert eye_aspect_ratio([(0, 0)] * 6) == 0.0


def rotation_x(degrees):
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return [[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]]


def test_head_pitch_from_transformation_matrix():
    assert head_pitch(rotation_x(0)) == 0
    assert math.isclose(head_pitch(rotation_x(25)), 25)
    assert math.isclose(head_pitch(rotation_x(-10)), -10)


POSED = Baseline(nose_y=0.50, face_width=0.30, open_ear=0.30, pitch=2.0)


def test_looking_down_at_notes_is_not_slouching():
    m = Monitor(POSED)
    reading = Sample(ear=0.30, nose_y=0.62, face_width=0.30, pitch=-28.0)
    alerts, _ = feed(m, reading, 0, 60)
    assert Alert.SLOUCH not in alerts
    assert m.status.looking_down
    assert not m.status.slouching


def test_sinking_while_facing_the_screen_is_slouching():
    m = Monitor(POSED)
    sunk = Sample(ear=0.30, nose_y=0.62, face_width=0.30, pitch=-4.0)
    alerts, _ = feed(m, sunk, 0, 31)
    assert Alert.SLOUCH in alerts


def test_old_baseline_without_pitch_still_works():
    m = Monitor(BASE)  # calibrated before pitch existed
    alerts, _ = feed(m, Sample(ear=0.30, nose_y=0.62, face_width=0.30, pitch=-28.0), 0, 31)
    assert Alert.SLOUCH in alerts


def test_baseline_takes_median_pitch():
    samples = [Sample(0.3, 0.5, 0.3, p) for p in (1.0, 3.0, 2.0)] + [Sample(0.3, 0.5, 0.3)]
    assert Baseline.from_samples(samples).pitch == 2.0
    assert Baseline.from_samples([GOOD]).pitch is None

def test_reminders_of_different_kinds_wait_for_the_global_gap():
    m = Monitor(BASE)
    slouch_and_close = Sample(ear=0.30, nose_y=0.62, face_width=0.42)
    alerts, t = feed(m, slouch_and_close, 0, 31)
    assert alerts == [Alert.SLOUCH]  # not both at once
    alerts, t = feed(m, slouch_and_close, t, 60)
    assert alerts == []  # 2 minutes haven't passed
    alerts, _ = feed(m, slouch_and_close, t, 61)
    assert alerts == [Alert.TOO_CLOSE]  # it was waiting, not dropped


def test_snooze_holds_back_all_reminders():
    gate = Gate()
    m = Monitor(BASE, gate=gate)
    gate.snooze(0, 15 * 60)
    alerts, t = feed(m, SLOUCH, 0, 15 * 60 - 1, step=1)
    assert alerts == []
    alerts, _ = feed(m, SLOUCH, t, 5, step=1)
    assert alerts == [Alert.SLOUCH]


def test_gate_is_shared_between_monitors():
    gate = Gate()
    first = Monitor(BASE, gate=gate)
    feed(first, SLOUCH, 0, 31)
    second = Monitor(BASE, gate=gate)  # e.g. after recalibrating
    alerts, _ = feed(second, SLOUCH, 31, 60)
    assert alerts == []
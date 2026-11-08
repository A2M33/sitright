"""Engine tests with a fake camera and a fake clock. No cv2 or mediapipe needed."""

import threading
from datetime import datetime

from sitright.engine import (
    CAMERA_DOWN,
    MODEL_MISSING,
    RETRY_SECONDS,
    CameraError,
    Engine,
    ModelError,
    describe_until,
    find_cameras,
    tomorrow_morning,
)
from sitright.config import Settings
from sitright.monitor import Baseline, Sample

BASE = Baseline(nose_y=0.50, face_width=0.30, open_ear=0.30)
GOOD = Sample(ear=0.30, nose_y=0.50, face_width=0.30)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakeDetector:
    """Plays back `script(now)`: a Sample, None (no face) or an exception to raise."""

    def __init__(self, script) -> None:
        self.script = script
        self.closed = False

    def read(self, now):
        result = self.script(now)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self) -> None:
        self.closed = True


class FakeView:
    def __init__(self, cancel_after: int | None = None) -> None:
        self.frames: list[list[str]] = []
        self.closed = False
        self.cancel_after = cancel_after

    def show(self, frame, lines):
        self.frames.append(lines)
        return self.cancel_after is None or len(self.frames) < self.cancel_after

    def close(self):
        self.closed = True


class Harness:
    def __init__(
        self,
        script,
        open_fails=lambda now: False,
        in_use=lambda now: [],
        baseline=BASE,
        view=None,
        locked=lambda now: False,
    ) -> None:
        self.clock = Clock()
        self.slept = 0.0  # wall-clock time the fake clock didn't see
        self.opens: list[float] = []
        self.tooltips: list[str] = []
        self.states: list[str] = []
        self.notes: list[str] = []
        self.cameras: list[int] = []
        self.saved: list[Settings] = []

        def opener(camera):
            self.opens.append(self.clock.t)
            self.cameras.append(camera)
            if open_fails(self.clock.t):
                raise CameraError(f"Can't open camera {camera}.")
            return FakeDetector(script)

        self.engine = Engine(
            0,
            lambda state, tip: (self.states.append(state), self.tooltips.append(tip)),
            lambda title, msg: self.notes.append(msg),
            settings=Settings(baseline=baseline),
            open_detector=opener,
            clock=self.clock,
            # Wall clock starts at 2026-03-02 10:00 local time, moves with the fake clock.
            wall_clock=lambda: datetime(2026, 3, 2, 10, 0).timestamp() + self.clock.t + self.slept,
            check_in_use=lambda: in_use(self.clock.t),
            check_locked=lambda: locked(self.clock.t),
            save=self.saved.append,
            calibration_view=lambda: view or FakeView(),
            sleep=self.advance,
        )

    def advance(self, seconds: float) -> None:
        self.clock.t += seconds

    def run_until(self, end: float) -> None:
        while self.clock.t < end:
            self.clock.t += max(self.engine.step(), 0.05)


def test_camera_failure_keeps_the_sitting_timer():
    # Camera works for 5 minutes, drops out for 20 seconds, then comes back.
    def script(now):
        return CameraError("unplugged") if 300 <= now < 320 else GOOD

    h = Harness(script)
    h.run_until(300)
    assert 4.9 < h.engine.monitor.status.sitting_minutes < 5.1
    h.run_until(310)
    assert h.engine.detector is None  # camera released while it's broken
    assert h.tooltips[-1] == CAMERA_DOWN
    assert h.states[-1] == "fault"  # the red lamp
    assert "no one at the desk" not in h.tooltips[-1]
    h.run_until(332)  # first retry is 30 s after the failure
    # Not treated as "you left": the timer and the blink window carried on.
    assert h.engine.monitor.status.face
    assert h.engine.monitor.status.sitting_minutes > 5.5
    assert h.engine.monitor.status.blinks_per_minute is not None


def test_camera_open_is_retried_every_30_seconds():
    h = Harness(lambda now: GOOD, open_fails=lambda now: True)
    h.run_until(65)
    assert len(h.opens) == 3
    assert h.opens[1] - h.opens[0] >= RETRY_SECONDS
    assert h.tooltips[-1] == CAMERA_DOWN
    assert len(h.notes) == 1  # one notification per outage, not one per retry


def test_tooltip_says_when_the_face_model_is_missing():
    h = Harness(lambda now: GOOD)
    attempts = []

    def no_model(camera):
        attempts.append(h.clock.t)
        raise ModelError("Couldn't download the face model. Check your internet connection.")

    h.engine._open_detector = no_model
    h.run_until(40)
    assert h.tooltips[-1] == MODEL_MISSING  # was a bare "SitRight: error"
    assert h.states[-1] == "fault"
    assert len(attempts) == 2 and len(h.notes) == 1


def test_engine_recovers_when_camera_comes_back():
    h = Harness(lambda now: GOOD, open_fails=lambda now: now < 40)
    h.run_until(70)
    assert h.engine.detector is not None
    assert h.engine.monitor.status.face
    assert h.tooltips[-1].startswith("SitRight: sitting")


def test_single_dropped_frame_is_not_a_camera_failure():
    h = Harness(lambda now: CameraError("glitch") if 10 <= now < 10.1 else GOOD)
    h.run_until(20)
    assert len(h.opens) == 1
    assert CAMERA_DOWN not in h.tooltips


def test_unexpected_error_does_not_kill_the_engine():
    def script(now):
        return ValueError("model hiccup") if 5 <= now < 5.1 else GOOD

    h = Harness(script)
    h.run_until(60)  # step() must never raise
    assert h.engine.detector is not None
    assert h.engine.monitor.status.face


def test_pause_releases_camera_and_resumes_on_time():
    h = Harness(lambda now: GOOD)
    h.run_until(10)
    detector = h.engine.detector
    h.engine.pause_for(30 * 60)
    h.run_until(20)
    assert h.engine.detector is None and detector.closed  # camera light goes off
    assert h.tooltips[-1] == "SitRight: paused until 10:30"
    h.run_until(10 + 30 * 60 + 1)
    assert not h.engine.paused
    assert h.engine.detector is not None
    assert len(h.opens) == 2


def test_resume_early():
    h = Harness(lambda now: GOOD)
    h.engine.pause_until_tomorrow()
    h.run_until(5)
    assert h.engine.paused
    assert h.tooltips[-1] == "SitRight: paused until tomorrow 06:00"
    h.engine.resume()
    h.run_until(6)
    assert h.engine.detector is not None


def test_resuming_from_the_tray_while_the_tooltip_is_built_is_not_an_error():
    h = Harness(lambda now: GOOD)
    h.engine.pause_for(60)
    clock = h.engine._wall

    def resumed_meanwhile():  # the tray thread clicks Resume mid-way
        h.engine._paused_until = None
        return clock()

    h.engine._wall = resumed_meanwhile
    assert h.engine.pause_text() == "paused until 10:01"  # used to raise TypeError


def test_tomorrow_morning():
    assert tomorrow_morning(datetime(2026, 3, 2, 22, 15)) == datetime(2026, 3, 3, 6, 0)
    assert tomorrow_morning(datetime(2026, 3, 2, 6, 0)) == datetime(2026, 3, 3, 6, 0)
    # Still up at 2 am: "tomorrow" means this morning.
    assert tomorrow_morning(datetime(2026, 3, 3, 2, 0)) == datetime(2026, 3, 3, 6, 0)


def test_describe_until():
    now = datetime(2026, 3, 2, 22, 0)
    assert describe_until(datetime(2026, 3, 2, 22, 30), now) == "until 22:30"
    assert describe_until(datetime(2026, 3, 3, 6, 0), now) == "until tomorrow 06:00"

def test_auto_pause_while_another_app_uses_the_camera():
    h = Harness(lambda now: GOOD, in_use=lambda now: ["Zoom.exe"] if 20 <= now < 80 else [])
    h.run_until(30)
    assert h.engine.detector is None  # let go so the call app can have it
    assert h.tooltips[-1] == "SitRight: paused, camera or mic in use by Zoom.exe"
    h.run_until(90)
    assert h.engine.detector is not None  # back on its own after the call
    assert h.engine.monitor.status.face


def test_auto_pause_can_be_turned_off():
    h = Harness(lambda now: GOOD, in_use=lambda now: ["Teams"])
    h.engine.set_auto_pause(False)
    h.run_until(10)
    assert h.engine.detector is not None

def test_switch_camera_reopens_saves_and_recalibrates():
    h = Harness(lambda now: GOOD)
    h.run_until(5)
    old = h.engine.detector
    h.engine.switch_camera(2)
    assert h.saved and h.saved[-1].camera == 2
    assert h.engine._calibrate.is_set()  # old pose doesn't fit the new camera angle
    h.engine._calibrate.clear()  # (calibration itself needs a real camera)
    h.run_until(6)
    assert old.closed
    assert h.cameras == [0, 2]


def test_find_cameras_lists_working_ones_and_the_current_one():
    assert find_cameras(lambda i: i in (1, 3)) == [1, 3]
    # Camera 0 is busy because we hold it; it still belongs in the list.
    assert find_cameras(lambda i: i in (1, 3), current=0) == [0, 1, 3]
    assert find_cameras(lambda i: False, current=7) == [7]

def test_snooze_from_the_menu_expires_after_15_minutes():
    h = Harness(lambda now: GOOD)
    h.engine.snooze()
    h.run_until(10)
    assert h.engine.snoozed
    assert h.tooltips[-1].endswith("reminders snoozed")
    h.run_until(15 * 60 + 1)
    assert not h.engine.snoozed

SLOUCH = Sample(ear=0.30, nose_y=0.62, face_width=0.30)


def test_sensitivity_applies_live_and_is_saved():
    h = Harness(lambda now: SLOUCH)
    h.engine.set_sensitivity("strict")
    assert h.saved[-1].sensitivity == "strict"
    h.run_until(22)  # strict: 30 s * 0.7 = 21 s hold
    assert "Sit up straight. You've been slouching for a bit." in h.notes
    # The saved numbers stay as the user wrote them; only the live copy is scaled.
    assert h.engine.settings.thresholds.hold_seconds == 30


def test_a_nod_does_not_turn_the_icon_amber():
    states = []
    h = Harness(lambda now: SLOUCH if now % 10 < 1 else GOOD)  # head dips 1 s in every 10
    h.engine.on_change = lambda state, tip: states.append((state, tip))
    h.run_until(60)
    assert {state for state, _ in states} == {"ok"}  # it used to flicker amber each time
    assert not any("slouching" in tip for _, tip in states)


def test_a_real_slouch_turns_the_icon_amber_within_seconds():
    states = []
    h = Harness(lambda now: GOOD if now < 10 else SLOUCH)
    h.engine.on_change = lambda state, tip: states.append((h.clock.t, state, tip))
    h.run_until(20)
    first = next(t for t, state, _ in states if state == "warn")
    assert 13 <= first < 13.2
    assert "slouching" in states[-1][2]


def test_switching_a_reminder_off_applies_live_and_is_saved():
    h = Harness(lambda now: SLOUCH)
    h.run_until(5)
    h.engine.set_reminder("slouch", False)
    assert h.saved[-1].reminders.slouch is False
    h.run_until(60)
    assert h.notes == []

def test_calibration_counts_down_then_captures_and_saves():
    view = FakeView()
    h = Harness(lambda now: GOOD, baseline=None, view=view)
    h.engine.request_calibration()
    h.run_until(10)
    firsts = [lines[0] for lines in view.frames]
    assert firsts[0] == "3"
    assert firsts.index("3") < firsts.index("2") < firsts.index("1") < firsts.index("Hold still")
    assert view.closed
    assert h.engine.monitor is not None
    assert h.saved[-1].baseline.nose_y == GOOD.nose_y
    assert h.notes[-1] == "Done. I'll let you know if you slouch."


def test_calibration_warns_when_pose_looks_off():
    close_up = Sample(ear=0.30, nose_y=0.50, face_width=0.62, nose_x=0.85)
    view = FakeView()
    h = Harness(lambda now: close_up, baseline=None, view=view)
    h.engine.request_calibration()
    h.run_until(10)
    assert any("very close" in line for lines in view.frames for line in lines)  # live hint
    assert h.notes[-1].startswith("Saved, but check this:")
    assert "very close" in h.notes[-1] and "off to the side" in h.notes[-1]


def test_closing_the_calibration_window_keeps_the_old_pose():
    view = FakeView(cancel_after=5)
    h = Harness(lambda now: Sample(ear=0.3, nose_y=0.4, face_width=0.3), view=view)
    h.engine.request_calibration()
    h.run_until(10)
    assert h.engine.monitor.baseline == BASE
    assert h.saved == []
    assert "cancelled" in h.notes[-1]
    assert view.closed

def test_engine_feeds_daily_stats():
    h = Harness(lambda now: GOOD if now < 100 else SLOUCH)
    h.run_until(200)
    stats = h.engine.stats.today
    assert stats.day == "2026-03-02"
    assert 45 <= stats.upright_percent <= 55
    assert stats.reminders == {"SLOUCH": 1}
    assert h.engine.stats_summary().startswith("Today: ")


def test_a_failing_tray_call_does_not_kill_the_engine_thread():
    calls = []
    alive = threading.Event()

    def on_change(state, tooltip):
        calls.append(tooltip)
        if len(calls) == 1:
            raise OSError("tray is gone")
        alive.set()

    engine = Engine(
        0,
        on_change,
        lambda title, message: None,
        settings=Settings(baseline=BASE),
        open_detector=lambda camera: FakeDetector(lambda now: GOOD),
    )
    engine.start()
    try:
        assert alive.wait(5)  # the thread used to die here and the tray froze
    finally:
        engine.stop()
        engine.join(5)


def test_camera_is_let_go_while_there_is_no_calibration():
    # First start, and the user cancelled the calibration window.
    view = FakeView(cancel_after=3)
    h = Harness(lambda now: GOOD, baseline=None, view=view)
    h.engine.request_calibration()
    h.run_until(10)
    assert h.engine.monitor is None
    assert h.engine.detector is None  # the light used to stay on with nothing being measured
    assert "not calibrated" in h.tooltips[-1]
    view.cancel_after = None
    h.engine.request_calibration()
    h.run_until(20)
    assert h.engine.monitor is not None


def test_a_dropped_frame_does_not_end_calibration():
    def script(now):
        return CameraError("glitch") if 4.0 <= now < 4.2 else GOOD  # during the capture

    view = FakeView()
    h = Harness(script, baseline=None, view=view)
    h.engine.request_calibration()
    h.run_until(10)
    assert h.engine.monitor is not None  # the window used to vanish with no pose saved
    assert h.notes[-1] == "Done. I'll let you know if you slouch."
    assert view.closed


def test_calibration_starts_over_when_the_camera_comes_back():
    def script(now):
        return CameraError("unplugged") if 1 <= now < 30 else GOOD

    h = Harness(script, baseline=None)
    h.engine.request_calibration()
    h.run_until(5)
    assert h.engine.detector is None
    assert h.tooltips[-1] == CAMERA_DOWN
    h.run_until(60)
    assert h.engine.monitor is not None
    assert h.notes[-1] == "Done. I'll let you know if you slouch."


def test_quitting_during_calibration_keeps_the_saved_pose():
    class QuitView(FakeView):
        def show(self, frame, lines):
            if lines[0] == "Hold still":
                h.engine.stop()
            return super().show(frame, lines)

    h = Harness(lambda now: Sample(ear=0.3, nose_y=0.4, face_width=0.3), view=QuitView())
    h.engine.request_calibration()
    h.engine.step()
    assert h.saved == []
    assert h.engine.monitor.baseline == BASE
    assert h.notes == []  # the tray icon is going away


def test_a_face_seen_in_a_few_frames_is_not_enough_to_calibrate():
    h = Harness(lambda now: GOOD if 4.0 <= now < 4.1 else None, baseline=None)
    h.engine.request_calibration()
    h.run_until(10)
    assert h.engine.monitor is None
    assert "couldn't see your face" in h.notes[-1]


def test_cancelling_the_first_calibration_does_not_mention_an_old_pose():
    h = Harness(lambda now: GOOD, baseline=None, view=FakeView(cancel_after=2))
    h.engine.request_calibration()
    h.run_until(5)
    assert h.notes[-1] == "Calibration cancelled. Calibrate from the tray menu."


def test_switching_camera_calibrates_even_without_a_saved_pose():
    h = Harness(lambda now: GOOD, baseline=None)
    h.engine.switch_camera(1)
    assert h.engine._calibrate.is_set()


def test_recalibrating_does_not_restart_the_break_timer():
    h = Harness(lambda now: GOOD)
    h.run_until(600)
    h.engine.request_calibration()
    h.run_until(610)
    assert h.engine.monitor.status.sitting_minutes > 10  # used to fall back to 0
    assert h.saved[-1].baseline == Baseline.from_samples([GOOD])


def test_a_failed_save_is_reported_and_is_not_a_camera_failure():
    h = Harness(lambda now: GOOD, baseline=None)

    def no_disk(_settings):
        raise OSError("disk full")

    h.engine._save = no_disk
    h.engine.request_calibration()
    h.run_until(10)
    assert h.engine.monitor is not None  # the pose still works for this run
    assert h.engine.detector is not None
    assert any("Couldn't save your settings" in note for note in h.notes)
    assert h.tooltips[-1].startswith("SitRight: sitting")


def test_camera_is_released_while_the_screen_is_locked():
    h = Harness(lambda now: GOOD, locked=lambda now: 300 <= now < 600)
    h.run_until(310)
    assert h.engine.detector is None  # the light used to stay on behind the lock screen
    assert h.tooltips[-1] == "SitRight: screen locked"
    h.run_until(610)
    assert h.engine.detector is not None
    assert h.engine.monitor.status.sitting_minutes < 1  # 5 minutes away reset the timer
    assert h.engine.stats.today.breaks == 1


def test_a_quick_lock_does_not_count_as_a_break():
    h = Harness(lambda now: GOOD, locked=lambda now: 300 <= now < 330)
    h.run_until(340)
    assert h.engine.monitor.status.sitting_minutes > 5
    assert h.engine.stats.today.breaks == 0


def test_a_failing_lock_check_is_ignored():
    def broken(now):
        raise OSError("no session info")

    h = Harness(lambda now: GOOD, locked=broken)
    h.run_until(5)
    assert h.engine.detector is not None


def test_an_empty_desk_is_checked_less_often():
    reads = []

    def script(now):
        reads.append(now)
        return None if 60 <= now < 120 else GOOD

    h = Harness(script)
    h.run_until(60)
    at_desk = len(reads)
    h.run_until(120)
    away = len(reads) - at_desk
    assert at_desk >= 60 * 19  # 20 FPS while someone is there, for the blinks
    assert away <= 60 * 5 + 40  # 5 FPS once the face has been gone past the grace time
    h.run_until(121)
    assert h.engine.monitor.status.face  # back at the desk within a fraction of a second
    assert h.engine.stats.today.breaks == 0


def test_waking_from_sleep_reopens_the_camera_and_counts_as_a_break():
    h = Harness(lambda now: GOOD)
    h.run_until(300)
    stale = h.engine.detector
    h.slept = 3600.0  # a clock that stops during sleep sees no gap here
    h.run_until(301)
    assert stale.closed
    assert len(h.opens) == 2
    assert h.engine.monitor.status.sitting_minutes < 1

"""Engine tests with a fake camera and a fake clock. No cv2 or mediapipe needed."""

from datetime import datetime

from sitright.engine import (
    CAMERA_DOWN,
    RETRY_SECONDS,
    CameraError,
    Engine,
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


class Harness:
    def __init__(self, script, open_fails=lambda now: False, in_use=lambda now: []) -> None:
        self.clock = Clock()
        self.opens: list[float] = []
        self.tooltips: list[str] = []
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
            lambda state, tip: self.tooltips.append(tip),
            lambda title, msg: self.notes.append(msg),
            settings=Settings(baseline=BASE),
            open_detector=opener,
            clock=self.clock,
            # Wall clock starts at 2026-03-02 10:00 local time, moves with the fake clock.
            wall_clock=lambda: datetime(2026, 3, 2, 10, 0).timestamp() + self.clock.t,
            check_in_use=lambda: in_use(self.clock.t),
            save=self.saved.append,
        )

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


def test_switching_a_reminder_off_applies_live_and_is_saved():
    h = Harness(lambda now: SLOUCH)
    h.run_until(5)
    h.engine.set_reminder("slouch", False)
    assert h.saved[-1].reminders.slouch is False
    h.run_until(60)
    assert h.notes == []
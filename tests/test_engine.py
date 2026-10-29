"""Engine tests with a fake camera and a fake clock. No cv2 or mediapipe needed."""

from sitright.engine import CAMERA_DOWN, RETRY_SECONDS, CameraError, Engine
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
    def __init__(self, script, open_fails=lambda now: False) -> None:
        self.clock = Clock()
        self.opens: list[float] = []
        self.tooltips: list[str] = []
        self.notes: list[str] = []

        def opener(camera):
            self.opens.append(self.clock.t)
            if open_fails(self.clock.t):
                raise CameraError(f"Can't open camera {camera}.")
            return FakeDetector(script)

        self.engine = Engine(
            0,
            lambda state, tip: self.tooltips.append(tip),
            lambda title, msg: self.notes.append(msg),
            baseline=BASE,
            open_detector=opener,
            clock=self.clock,
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

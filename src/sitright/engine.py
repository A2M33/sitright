"""Background loop: camera -> detector -> monitor -> tray callbacks.

No UI and no cv2 import at module level, so tests can drive `Engine.step`
with a fake detector and a fake clock.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from . import config
from .monitor import Baseline, Monitor, Sample, Status, Thresholds

CALIBRATION_SECONDS = 3.0
FPS = 12  # enough to catch blinks (~150 ms) without keeping a CPU core busy
RETRY_SECONDS = 30.0  # how often to try the camera again after it failed
MAX_READ_FAILURES = 10  # consecutive bad frames before we give the camera up

CAMERA_DOWN = "SitRight: camera busy / not found"


class CameraError(RuntimeError):
    """The camera could not be opened or stopped delivering frames."""


def open_detector(camera: int):
    from .detector import Detector  # imported late: pulls in cv2 and mediapipe

    return Detector(camera)


class Engine(threading.Thread):
    def __init__(
        self,
        camera: int,
        on_change: Callable[[str, str], None],
        on_notify: Callable[[str, str], None],
        baseline: Baseline | None = None,
        thresholds: Thresholds | None = None,
        open_detector: Callable[[int], object] = open_detector,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(daemon=True)
        self.camera = camera
        self.on_change = on_change  # (state, tooltip) -> None
        self.on_notify = on_notify  # (title, message) -> None
        self.paused = False
        self.thresholds = thresholds or Thresholds()
        self.monitor = Monitor(baseline, self.thresholds) if baseline else None
        self.detector = None
        self._open_detector = open_detector
        self._clock = clock
        self._stop_event = threading.Event()
        self._calibrate = threading.Event()
        self._retry_at: float | None = None
        self._read_failures = 0
        self._camera_down = False  # last failure was the camera (vs. some other error)
        self._failing = False  # no good frame since the last failure

    def request_calibration(self) -> None:
        self._calibrate.set()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        if self.monitor is None:
            self._calibrate.set()
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(self.step())
        finally:
            self._release()

    def step(self) -> float:
        """Do one unit of work and return how long to wait before the next one."""
        now = self._clock()
        if self.paused:
            # Release the camera so its light turns off while paused.
            self._release()
            self.on_change("idle", "SitRight: paused")
            return 0.5
        if self.detector is None and not self._open_camera(now):
            return 0.5
        try:
            if self._calibrate.is_set():
                self._calibrate.clear()
                self._run_calibration()
                return 0.0
            if self.monitor is None:
                self.on_change("idle", "SitRight: not calibrated (right-click > Calibrate)")
                return 0.5
            sample = self.detector.read(now)
        except CameraError as exc:
            return self._read_failed(now, exc)
        except Exception as exc:  # never let one bad frame kill the thread for good
            self._camera_failed(now, exc)
            return 0.5
        self._read_failures = 0
        self._failing = self._camera_down = False
        for alert in self.monitor.update(sample, now):
            self.on_notify("SitRight", alert.value)
        self._report(self.monitor.status)
        return max(0.0, 1 / FPS - (self._clock() - now))

    def _open_camera(self, now: float) -> bool:
        if self._retry_at is not None and now < self._retry_at:
            self.on_change("idle", CAMERA_DOWN if self._camera_down else "SitRight: error")
            return False
        try:
            self.detector = self._open_detector(self.camera)
        except Exception as exc:
            self._camera_failed(now, exc)
            return False
        self._retry_at = None
        return True

    def _read_failed(self, now: float, exc: Exception) -> float:
        # A dropped frame now and then is normal; a run of them means the camera is gone.
        # Either way the monitor isn't told "no face", so the sitting timer keeps going.
        self._read_failures += 1
        if self._read_failures >= MAX_READ_FAILURES:
            self._camera_failed(now, exc)
            return 0.5
        return 1 / FPS

    def _camera_failed(self, now: float, exc: Exception) -> None:
        self._release()
        self._read_failures = 0
        self._retry_at = now + RETRY_SECONDS
        self._camera_down = isinstance(exc, CameraError)
        self.on_change("idle", CAMERA_DOWN if self._camera_down else "SitRight: error")
        if not self._failing:  # tell the user once per outage, not every 30 seconds
            self._failing = True
            self.on_notify("SitRight", f"{exc} Trying again every {RETRY_SECONDS:.0f} seconds.")

    def _release(self) -> None:
        if self.detector is not None:
            try:
                self.detector.close()
            except Exception:
                pass
            self.detector = None

    def _run_calibration(self) -> None:
        self.on_change("idle", "SitRight: calibrating")
        self.on_notify(
            "SitRight", "Calibrating: sit the way you want to sit and look at the screen."
        )
        self._stop_event.wait(2.0)  # time to settle into position
        samples: list[Sample] = []
        end = self._clock() + CALIBRATION_SECONDS
        while self._clock() < end and not self._stop_event.is_set():
            sample = self.detector.read(self._clock())
            if sample:
                samples.append(sample)
            self._stop_event.wait(1 / FPS)
        try:
            baseline = Baseline.from_samples(samples)
        except ValueError:
            self.on_notify("SitRight", "I couldn't see your face. Check the camera and try again.")
            return
        config.save(baseline, self.thresholds)
        self.monitor = Monitor(baseline, self.thresholds)
        self.on_notify("SitRight", "Done. I'll let you know if you slouch.")

    def _report(self, s: Status) -> None:
        if not s.face:
            self.on_change("idle", "SitRight: no one at the desk")
            return
        parts = []
        if s.slouching:
            parts.append("slouching")
        if s.too_close:
            parts.append("too close")
        if s.blinks_per_minute is not None:
            parts.append(f"{s.blinks_per_minute:.0f} blinks/min")
        parts.append(f"sitting {s.sitting_minutes:.0f} min")
        self.on_change("ok" if s.ok else "warn", "SitRight: " + ", ".join(parts))

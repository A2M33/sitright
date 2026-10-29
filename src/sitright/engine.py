"""Background loop: camera -> detector -> monitor -> tray callbacks.

No UI and no cv2 import at module level, so tests can drive `Engine.step`
with a fake detector and a fake clock.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Callable

from . import config
from .monitor import Baseline, Monitor, Sample, Status, Thresholds

CALIBRATION_SECONDS = 3.0
FPS = 12  # enough to catch blinks (~150 ms) without keeping a CPU core busy
RETRY_SECONDS = 30.0  # how often to try the camera again after it failed
MAX_READ_FAILURES = 10  # consecutive bad frames before we give the camera up
IN_USE_POLL_SECONDS = 5.0  # how often to check whether a call app grabbed the camera/mic

CAMERA_DOWN = "SitRight: camera busy / not found"
DAY_STARTS_AT = 6  # "pause until tomorrow" ends at this hour


class CameraError(RuntimeError):
    """The camera could not be opened or stopped delivering frames."""


def tomorrow_morning(now: datetime) -> datetime:
    """When "pause until tomorrow" ends: the next 06:00 (today's, if it's still night)."""
    morning = now.replace(hour=DAY_STARTS_AT, minute=0, second=0, microsecond=0)
    return morning if now < morning else morning + timedelta(days=1)


def describe_until(until: datetime, now: datetime) -> str:
    if until.date() == now.date():
        return f"until {until:%H:%M}"
    if until.date() == now.date() + timedelta(days=1):
        return f"until tomorrow {until:%H:%M}"
    return f"until {until:%a %H:%M}"


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
        wall_clock: Callable[[], float] = time.time,
        check_in_use: Callable[[], list[str]] = lambda: [],
    ) -> None:
        super().__init__(daemon=True)
        self.camera = camera
        self.on_change = on_change  # (state, tooltip) -> None
        self.on_notify = on_notify  # (title, message) -> None
        self.on_menu = lambda: None  # called when something shown in the menu changes
        self.thresholds = thresholds or Thresholds()
        self.monitor = Monitor(baseline, self.thresholds) if baseline else None
        self.detector = None
        self._open_detector = open_detector
        self._clock = clock
        self._wall = wall_clock
        self._paused_until: float | None = None  # wall-clock time; inf = until resumed
        self._was_paused = False
        self.auto_pause = True  # step aside while another app uses the camera or mic
        self._check_in_use = check_in_use
        self._busy: list[str] = []
        self._next_in_use_check = 0.0
        self._stop_event = threading.Event()
        self._calibrate = threading.Event()
        self._retry_at: float | None = None
        self._read_failures = 0
        self._camera_down = False  # last failure was the camera (vs. some other error)
        self._failing = False  # no good frame since the last failure

    @property
    def paused(self) -> bool:
        return self._paused_until is not None and self._wall() < self._paused_until

    def pause_for(self, seconds: float) -> None:
        self._paused_until = self._wall() + seconds
        self.on_menu()

    def pause_until_tomorrow(self) -> None:
        now = datetime.fromtimestamp(self._wall())
        self._paused_until = tomorrow_morning(now).timestamp()
        self.on_menu()

    def resume(self) -> None:
        self._paused_until = None
        self.on_menu()

    def pause_text(self) -> str:
        if not self.paused:
            return ""
        if self._paused_until == float("inf"):
            return "paused"
        now = datetime.fromtimestamp(self._wall())
        return "paused " + describe_until(datetime.fromtimestamp(self._paused_until), now)

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
        paused = self.paused
        if paused != self._was_paused:
            self._was_paused = paused
            self.on_menu()  # a timed pause ran out: hide "Resume" again
        if paused:
            # Release the camera so its light turns off while paused.
            self._release()
            self.on_change("idle", "SitRight: " + self.pause_text())
            return 0.5
        busy = self._others_using_devices(now)
        if busy:
            self._release()
            apps = " and ".join(busy[:2])
            self.on_change("idle", f"SitRight: paused, camera or mic in use by {apps}")
            return 1.0
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

    def _others_using_devices(self, now: float) -> list[str]:
        if not self.auto_pause:
            return []
        if now >= self._next_in_use_check:
            self._next_in_use_check = now + IN_USE_POLL_SECONDS
            try:
                self._busy = list(self._check_in_use())
            except Exception:  # a registry hiccup must not stop the app
                self._busy = []
        return self._busy

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
        if s.looking_down:
            parts.append("looking down")
        if s.blinks_per_minute is not None:
            parts.append(f"{s.blinks_per_minute:.0f} blinks/min")
        parts.append(f"sitting {s.sitting_minutes:.0f} min")
        self.on_change("ok" if s.ok else "warn", "SitRight: " + ", ".join(parts))

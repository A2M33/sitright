"""Background loop: camera -> detector -> monitor -> tray callbacks.

No UI and no cv2 import at module level, so tests can drive `Engine.step`
with a fake detector and a fake clock.
"""

from __future__ import annotations

import threading
import time
import traceback
from datetime import date, datetime, timedelta
from typing import Callable, Protocol

from . import config
from .stats import StatsTracker
from .monitor import (
    Alert,
    Baseline,
    Gate,
    Monitor,
    Sample,
    Status,
    calibration_phase,
    pose_warnings,
)

COUNTDOWN = 3  # seconds of 3-2-1 before capture starts
CALIBRATION_SECONDS = 3.0
MIN_CALIBRATION_SAMPLES = 5  # fewer frames with a face than this is too little to trust
# A blink lasts 100-150 ms. At 12 FPS a quick one could fall between two frames;
# 20 FPS gives at least two frames per blink and still leaves the CPU mostly idle.
FPS = 20
# With nobody in view there are no blinks to catch, and looking for a face in an
# empty picture is the most expensive thing the model does. 5 FPS still notices
# you sitting down within a fifth of a second.
AWAY_FPS = 5
RETRY_SECONDS = 30.0  # how often to try the camera again after it failed
MAX_READ_FAILURES = 10  # consecutive bad frames before we give the camera up
IN_USE_POLL_SECONDS = 5.0  # how often to check whether a call app grabbed the camera/mic
LOCK_POLL_SECONDS = 2.0  # how often to check whether the screen is locked
WARN_AFTER_SECONDS = 3.0  # bad posture turns the icon amber once it lasts this long

CAMERA_DOWN = "SitRight: camera busy or not found"
MODEL_MISSING = "SitRight: can't download the face model, retrying"
DAY_STARTS_AT = 6  # "pause until tomorrow" ends at this hour
SNOOZE_SECONDS = 15 * 60


class CameraError(RuntimeError):
    """The camera could not be opened or stopped delivering frames."""


class ModelError(RuntimeError):
    """The face model isn't on disk and couldn't be downloaded."""


class CalibrationView(Protocol):
    def show(self, frame, lines: list[str]) -> bool:
        """Draw the frame with `lines` on top. False = the user closed the window."""

    def close(self) -> None: ...


class NoView:
    def show(self, frame, lines: list[str]) -> bool:
        return True

    def close(self) -> None:
        pass


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


def camera_works(index: int) -> bool:
    from .detector import camera_works

    return camera_works(index)


def find_cameras(
    works: Callable[[int], bool] = camera_works, current: int | None = None, upto: int = 5
) -> list[int]:
    """Camera indices that open and deliver a picture.

    `current` is always listed: we may be holding it open ourselves, so a second
    open can fail even though it works fine.
    """
    found = [i for i in range(upto) if i == current or works(i)]
    if current is not None and current not in found:
        found.append(current)
    return sorted(found)


class Engine(threading.Thread):
    def __init__(
        self,
        camera: int,
        on_change: Callable[[str, str], None],
        on_notify: Callable[[str, str], None],
        settings: config.Settings | None = None,
        open_detector: Callable[[int], object] = open_detector,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        check_in_use: Callable[[], list[str]] = lambda: [],
        check_locked: Callable[[], bool] = lambda: False,
        save: Callable[[config.Settings], None] = config.save,
        calibration_view: Callable[[], CalibrationView] = lambda: NoView(),
        stats: StatsTracker | None = None,
        sleep: Callable[[float], object] | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.camera = camera
        self.on_change = on_change  # (state, tooltip) -> None
        self.on_notify = on_notify  # (title, message) -> None
        self.on_menu = lambda: None  # called when something shown in the menu changes
        self.settings = settings or config.Settings()
        self.gate = Gate()  # min gap between reminders and snooze, kept across recalibration
        self._was_snoozed = False
        self.monitor = self._new_monitor(self.settings.baseline)
        self.detector = None
        self._camera_changed = False
        self._save = save
        self._open_detector = open_detector
        self._clock = clock
        self._wall = wall_clock
        self._paused_until: float | None = None  # wall-clock time
        self._was_paused = False
        self._check_in_use = check_in_use
        self._busy: list[str] = []
        self._next_in_use_check = 0.0
        self._check_locked = check_locked
        self._locked = False
        self._next_lock_check = 0.0
        self._last_wall: float | None = None
        self._stop_event = threading.Event()
        self._sleep = sleep or self._stop_event.wait
        self._calibration_view = calibration_view
        self.stats = stats or StatsTracker(self.settings.thresholds.away_reset_seconds)
        self._summary = ""
        self._calibrate = threading.Event()
        self._retry_at: float | None = None
        self._read_failures = 0
        self._failure_tip = CAMERA_DOWN  # tooltip until the next retry, set on each failure
        self._failing = False  # no good frame since the last failure

    def _new_monitor(self, baseline: Baseline | None) -> Monitor | None:
        # Shares the reminders object, so menu toggles apply on the next frame.
        s = self.settings
        return Monitor(baseline, self._thresholds(), s.reminders, self.gate) if baseline else None

    def _thresholds(self):
        return self.settings.thresholds.scaled(self.settings.sensitivity)

    def _persist(self) -> None:
        try:
            self._save(self.settings)
        except OSError as exc:  # the setting still applies until the next start
            self.on_notify("SitRight", f"Couldn't save your settings: {exc}")

    # Tray settings: each one applies on the next frame and is saved right away.

    def set_sensitivity(self, name: str) -> None:
        self.settings.sensitivity = name
        if self.monitor is not None:
            self.monitor.thresholds = self._thresholds()
        self._persist()
        self.on_menu()

    def set_reminder(self, key: str, on: bool) -> None:
        setattr(self.settings.reminders, key, on)
        self._persist()
        self.on_menu()

    def set_auto_pause(self, on: bool) -> None:
        self.settings.auto_pause = on
        self._next_in_use_check = 0.0
        self._persist()
        self.on_menu()

    @property
    def paused(self) -> bool:
        until = self._paused_until  # once: Resume on the tray thread may clear it meanwhile
        return until is not None and self._wall() < until

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

    @property
    def snoozed(self) -> bool:
        return self.gate.snoozed(self._clock())

    def snooze(self) -> None:
        self.gate.snooze(self._clock(), SNOOZE_SECONDS)
        self.on_menu()

    def unsnooze(self) -> None:
        self.gate.unsnooze()
        self.on_menu()

    def switch_camera(self, index: int) -> None:
        """Use another camera from the next frame on, and remember it."""
        if index == self.camera:
            return
        self.camera = self.settings.camera = index
        self._persist()
        self._camera_changed = True  # the engine thread releases the old camera
        self._retry_at = None
        # A calibrated pose belongs to the old camera's angle and distance.
        self.request_calibration()
        self.on_menu()

    def pause_text(self) -> str:
        # Read once: the tray thread may resume between a check and a second read.
        until, wall = self._paused_until, self._wall()
        if until is None or wall >= until:
            return ""
        now = datetime.fromtimestamp(wall)
        return "paused " + describe_until(datetime.fromtimestamp(until), now)

    def request_calibration(self) -> None:
        self._calibrate.set()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        if self.monitor is None:
            self._calibrate.set()
        try:
            while not self._stop_event.is_set():
                try:
                    delay = self.step()
                except Exception:  # e.g. the tray failed: keep watching, or the app goes quiet
                    traceback.print_exc()
                    delay = 1.0
                self._stop_event.wait(delay)
        finally:
            self._release()
            self.stats.flush()

    def stats_summary(self) -> str:
        return self.stats.summary()

    def step(self) -> float:
        """Do one unit of work and return how long to wait before the next one."""
        now = self._clock()
        self._notice_sleep()
        if self._camera_changed:
            self._camera_changed = False
            self._release()
        paused, snoozed = self.paused, self.snoozed
        if (paused, snoozed) != (self._was_paused, self._was_snoozed):
            self._was_paused, self._was_snoozed = paused, snoozed
            self.on_menu()  # a timed pause or snooze ran out: update the menu
        if paused:
            # Release the camera so its light turns off while paused.
            self._release()
            self.on_change("idle", "SitRight: " + self.pause_text())
            return 0.5
        if self._screen_locked(now):
            # Nobody to watch, and a lit camera on a locked PC looks wrong.
            self._release()
            self._nobody_here(now)
            self.on_change("idle", "SitRight: screen locked")
            return 1.0
        busy = self._others_using_devices(now)
        if busy:
            self._release()
            apps = " and ".join(busy[:2])
            self.on_change("idle", f"SitRight: paused, camera or mic in use by {apps}")
            return 1.0
        if self.monitor is None and not self._calibrate.is_set():
            # Nothing to compare frames with, so don't keep the camera light on.
            self._release()
            self.on_change("idle", "SitRight: not calibrated (right-click > Calibrate)")
            return 0.5
        if self.detector is None and not self._open_camera(now):
            return 0.5
        try:
            if self._calibrate.is_set():
                self._calibrate.clear()
                self._run_calibration()
                return 0.0
            sample = self.detector.read(now)
        except CameraError as exc:
            return self._read_failed(now, exc)
        except Exception as exc:  # never let one bad frame kill the thread for good
            self._camera_failed(now, exc)
            return 0.5
        self._read_failures = 0
        self._failing = False
        today = date.fromtimestamp(self._wall())
        for alert in self.monitor.update(sample, now):
            self.on_notify("SitRight", alert.value)
            self.stats.reminder(alert.name, today)
        status = self.monitor.status
        self.stats.observe(status.face, status.ok, now, today)
        summary = self.stats.summary()
        if summary != self._summary:  # changes about once a minute at most
            self._summary = summary
            self.on_menu()
        self._report(status, now)
        fps = FPS if status.face else AWAY_FPS
        return max(0.0, 1 / fps - (self._clock() - now))

    def _notice_sleep(self) -> None:
        """A long jump in wall-clock time means the PC slept. Monotonic clocks may not show it."""
        wall = self._wall()
        last, self._last_wall = self._last_wall, wall
        if last is None or wall - last < self.settings.thresholds.away_reset_seconds:
            return
        self._release()  # camera handles rarely survive sleep; don't wait for reads to fail
        self._retry_at = None
        if self.monitor is not None:
            self.monitor.forget()

    def _screen_locked(self, now: float) -> bool:
        if now >= self._next_lock_check:
            self._next_lock_check = now + LOCK_POLL_SECONDS
            try:
                self._locked = bool(self._check_locked())
            except Exception:
                self._locked = False
        return self._locked

    def _nobody_here(self, now: float) -> None:
        """Count this moment as "no face", without a camera, so breaks still add up."""
        if self.monitor is not None:
            self.monitor.update(None, now)
        self.stats.observe(False, False, now, date.fromtimestamp(self._wall()))

    def _others_using_devices(self, now: float) -> list[str]:
        if not self.settings.auto_pause:
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
            self.on_change("fault", self._failure_tip)
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
        if isinstance(exc, CameraError):
            self._failure_tip = CAMERA_DOWN
        elif isinstance(exc, ModelError):  # the notification is easy to miss on first start
            self._failure_tip = MODEL_MISSING
        else:
            self._failure_tip = "SitRight: error"
        self.on_change("fault", self._failure_tip)
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
        """Countdown, then a few seconds of capture, shown in a small preview window."""
        self.on_change("idle", "SitRight: calibrating")
        view = self._calibration_view()
        samples: list[Sample] = []
        failures = 0
        start = self._clock()
        try:
            while not self._stop_event.is_set():
                now = self._clock()
                phase, count = calibration_phase(now - start, COUNTDOWN, CALIBRATION_SECONDS)
                if phase == "done":
                    break
                try:
                    sample = self.detector.read(now)
                except CameraError as exc:
                    # Same rule as normal running: a dropped frame is no reason to give up.
                    failures += 1
                    if failures >= MAX_READ_FAILURES:
                        self._calibrate.set()  # start over once the camera is back
                        self._camera_failed(now, exc)
                        return
                    self._sleep(1 / FPS)
                    continue
                failures = 0
                if phase == "capture" and sample:
                    samples.append(sample)
                if phase == "countdown":
                    hints = pose_warnings([sample]) if sample else ["I can't see your face yet."]
                    lines = [str(count), "Sit the way you want to sit"] + hints[:1]
                else:
                    hint = "Look at the screen" if sample else "I can't see your face."
                    lines = ["Hold still", hint]
                if not view.show(getattr(self.detector, "last_frame", None), lines):
                    kept = "Your old pose is kept." if self.monitor else "Calibrate from the tray menu."
                    self.on_notify("SitRight", f"Calibration cancelled. {kept}")
                    return
                self._sleep(1 / FPS)
        finally:
            view.close()
        if self._stop_event.is_set():
            return  # quitting: a cut-off capture must not replace the saved pose
        if len(samples) < MIN_CALIBRATION_SAMPLES:
            self.on_notify("SitRight", "I couldn't see your face. Check the camera and try again.")
            return
        baseline = Baseline.from_samples(samples)
        self.settings.baseline = baseline
        self._persist()
        if self.monitor is None:
            self.monitor = self._new_monitor(baseline)
        else:
            self.monitor.recalibrate(baseline)
        warnings = pose_warnings(samples)
        if warnings:
            self.on_notify("SitRight", "Saved, but check this: " + " ".join(warnings))
        else:
            self.on_notify("SitRight", "Done. I'll let you know if you slouch.")

    def _report(self, s: Status, now: float) -> None:
        if not s.face:
            self.on_change("idle", "SitRight: no one at the desk")
            return
        # A nod or a reach for the mouse dips the head for a moment. Only show
        # what has lasted a few seconds, or the icon flickers amber all day.
        slouching = self.monitor.bad_for(Alert.SLOUCH, now) >= WARN_AFTER_SECONDS
        too_close = self.monitor.bad_for(Alert.TOO_CLOSE, now) >= WARN_AFTER_SECONDS
        parts = []
        if slouching:
            parts.append("slouching")
        if too_close:
            parts.append("too close")
        if s.looking_down:
            parts.append("looking down")
        if s.blinks_per_minute is not None:
            parts.append(f"{s.blinks_per_minute:.0f} blinks/min")
        parts.append(f"sitting {s.sitting_minutes:.0f} min")
        if self.snoozed:
            parts.append("reminders snoozed")
        state = "warn" if slouching or too_close else "ok"
        self.on_change(state, "SitRight: " + ", ".join(parts))

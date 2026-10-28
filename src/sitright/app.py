"""Tray app: a background thread watches the camera, the tray icon shows the result."""

from __future__ import annotations

import threading
import time

from PIL import Image, ImageDraw

from . import config
from .detector import Detector
from .monitor import Baseline, Monitor, Sample, Status

CALIBRATION_SECONDS = 3.0
FPS = 12  # enough to catch blinks (~150 ms) without keeping a CPU core busy

COLORS = {"ok": "#3fb950", "warn": "#d29922", "idle": "#6e7681"}


def make_icon(state: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=COLORS[state])
    # A tiny seated figure: head and a straight back.
    d.ellipse((25, 12, 39, 26), fill="white")
    d.rounded_rectangle((27, 28, 37, 50), radius=4, fill="white")
    return img


class Engine(threading.Thread):
    def __init__(self, camera: int, on_change, on_notify) -> None:
        super().__init__(daemon=True)
        self.camera = camera
        self.on_change = on_change  # (state, tooltip) -> None
        self.on_notify = on_notify  # (title, message) -> None
        self.paused = False
        self._stop_event = threading.Event()
        self._calibrate = threading.Event()
        baseline, self.thresholds = config.load()
        self.monitor = Monitor(baseline, self.thresholds) if baseline else None

    def request_calibration(self) -> None:
        self._calibrate.set()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        detector: Detector | None = None
        if self.monitor is None:
            self._calibrate.set()
        try:
            while not self._stop_event.is_set():
                if self.paused:
                    # Release the camera so its light turns off while paused.
                    if detector:
                        detector.close()
                        detector = None
                    self.on_change("idle", "SitRight: paused")
                    self._stop_event.wait(0.5)
                    continue
                if detector is None:
                    detector = Detector(self.camera)
                if self._calibrate.is_set():
                    self._calibrate.clear()
                    self._run_calibration(detector)
                    continue
                started = time.monotonic()
                if self.monitor is None:
                    self.on_change("idle", "SitRight: not calibrated (right-click > Calibrate)")
                    self._stop_event.wait(0.5)
                    continue
                sample = detector.read(started)
                for alert in self.monitor.update(sample, started):
                    self.on_notify("SitRight", alert.value)
                self._report(self.monitor.status)
                self._stop_event.wait(max(0.0, 1 / FPS - (time.monotonic() - started)))
        except Exception as exc:  # show the problem instead of dying silently in the tray
            self.on_change("idle", "SitRight: error")
            self.on_notify("SitRight stopped", str(exc))
        finally:
            if detector:
                detector.close()

    def _run_calibration(self, detector: Detector) -> None:
        self.on_change("idle", "SitRight: calibrating")
        self.on_notify(
            "SitRight", "Calibrating: sit the way you want to sit and look at the screen."
        )
        self._stop_event.wait(2.0)  # time to settle into position
        samples: list[Sample] = []
        end = time.monotonic() + CALIBRATION_SECONDS
        while time.monotonic() < end and not self._stop_event.is_set():
            sample = detector.read(time.monotonic())
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


def notify(icon, title: str, message: str) -> None:
    if icon.HAS_NOTIFICATION:
        icon.notify(message, title)
        return
    try:
        from plyer import notification

        notification.notify(title=title, message=message, app_name="SitRight", timeout=8)
    except Exception:
        print(f"{title}: {message}")


def run_tray(camera: int = 0) -> None:
    import pystray

    state = {"current": None}

    def on_change(new_state: str, tooltip: str) -> None:
        if state["current"] != new_state:
            state["current"] = new_state
            icon.icon = make_icon(new_state)
        icon.title = tooltip

    engine = Engine(camera, on_change, lambda t, m: notify(icon, t, m))

    def toggle_pause(_icon, _item) -> None:
        engine.paused = not engine.paused

    def calibrate(_icon, _item) -> None:
        engine.paused = False
        engine.request_calibration()

    def quit_app(_icon, _item) -> None:
        engine.stop()
        icon.stop()

    icon = pystray.Icon(
        "sitright",
        make_icon("idle"),
        "SitRight",
        menu=pystray.Menu(
            pystray.MenuItem("Pause", toggle_pause, checked=lambda _: engine.paused),
            pystray.MenuItem("Calibrate (sit straight first)", calibrate),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", quit_app),
        ),
    )

    def setup(icon) -> None:
        icon.visible = True
        engine.start()

    icon.run(setup)


def run_preview(camera: int = 0) -> None:
    """Camera window with live numbers. Handy for tuning and for recording a demo."""
    import cv2

    baseline, thresholds = config.load()
    detector = Detector(camera)
    monitor = Monitor(baseline, thresholds) if baseline else None
    calib: list[Sample] = []
    calib_until = 0.0
    try:
        while True:
            now = time.monotonic()
            sample = detector.read(now)
            frame = detector.last_frame
            if frame is None:
                break
            frame = cv2.flip(frame, 1)
            lines: list[tuple[str, tuple[int, int, int]]] = []
            if now < calib_until:
                if sample:
                    calib.append(sample)
                lines.append(("Calibrating... sit straight", (0, 200, 255)))
            elif calib:
                try:
                    baseline = Baseline.from_samples(calib)
                    config.save(baseline, thresholds)
                    monitor = Monitor(baseline, thresholds)
                except ValueError:
                    pass
                calib = []
            if monitor is None and now >= calib_until:
                lines.append(("Press C to calibrate", (0, 200, 255)))
            elif monitor and now >= calib_until:
                alerts = monitor.update(sample, now)
                s = monitor.status
                green, amber = (80, 185, 63), (34, 153, 210)
                lines.append(("Posture: " + ("slouching" if s.slouching else "good"),
                              amber if s.slouching else green))
                lines.append(("Distance: " + ("too close" if s.too_close else "good"),
                              amber if s.too_close else green))
                bpm = "measuring..." if s.blinks_per_minute is None else f"{s.blinks_per_minute:.0f}/min"
                lines.append((f"Blinks: {bpm}", (230, 230, 230)))
                for alert in alerts:
                    print(alert.value)
            for i, (text, color) in enumerate(lines):
                cv2.putText(frame, text, (16, 36 + i * 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                            (0, 0, 0), 4, cv2.LINE_AA)
                cv2.putText(frame, text, (16, 36 + i * 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                            color, 2, cv2.LINE_AA)
            cv2.imshow("SitRight preview (C = calibrate, Q = quit)", frame)
            key = cv2.waitKey(max(1, int(1000 / FPS))) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("c"):
                calib, calib_until = [], time.monotonic() + CALIBRATION_SECONDS
    finally:
        detector.close()
        cv2.destroyAllWindows()

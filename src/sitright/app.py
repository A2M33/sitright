"""Tray app: a background thread watches the camera, the tray icon shows the result."""

from __future__ import annotations

import sys
import threading
import time

from PIL import Image, ImageDraw

from . import autostart, config, inuse, stats
from .engine import (
    CALIBRATION_SECONDS,
    COUNTDOWN,
    FPS,
    CameraError,
    Engine,
    find_cameras,
    open_detector,
)
from .monitor import (
    SENSITIVITY,
    Baseline,
    Monitor,
    Sample,
    calibration_phase,
    pose_warnings,
)

COLORS = {"ok": "#3fb950", "warn": "#d29922", "idle": "#6e7681"}
REMINDER_LABELS = {
    "slouch": "Sit up straight",
    "too_close": "Lean back",
    "blink": "Blink (experimental)",
    "take_break": "Take a break",
}


def make_icon(state: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=COLORS[state])
    # A tiny seated figure: head and a straight back.
    d.ellipse((25, 12, 39, 26), fill="white")
    d.rounded_rectangle((27, 28, 37, 50), radius=4, fill="white")
    return img


def notify(icon, title: str, message: str) -> None:
    if icon.HAS_NOTIFICATION:
        icon.notify(message, title)
        return
    try:
        from plyer import notification

        notification.notify(title=title, message=message, app_name="SitRight", timeout=8)
    except Exception:
        print(f"{title}: {message}")


class TrayUpdater:
    """Pushes state to the tray icon only when it changed.

    The engine reports 20 times a second; every icon or title write is a call into
    the shell, so repeating an unchanged value is wasted work.
    """

    def __init__(self, get_icon) -> None:
        self._get_icon = get_icon
        self.state: str | None = None
        self.title: str | None = None

    def update(self, state: str, tooltip: str) -> None:
        icon = self._get_icon()
        if state != self.state:
            self.state = state
            icon.icon = make_icon(state)
        if tooltip != self.title:
            self.title = tooltip
            icon.title = tooltip[:127]  # Windows cuts tray tooltips at 127 characters


def run_tray(camera: int | None = None) -> None:
    """`camera` overrides the saved choice for this run only."""
    import pystray

    tray = TrayUpdater(lambda: icon)
    on_change = tray.update

    settings, problems = config.read()
    for problem in problems:
        print("config.json:", problem)
    engine = Engine(
        settings.camera if camera is None else camera,
        on_change,
        lambda t, m: notify(icon, t, m),
        settings,
        check_in_use=inuse.others_using_camera_or_mic,
        calibration_view=CvCalibrationView,
        stats=stats.StatsTracker(
            settings.thresholds.away_reset_seconds, stats.load_day, stats.save_day
        ),
    )

    engine.on_menu = lambda: icon.update_menu()

    cameras = {"found": [engine.camera], "scanning": False}

    def scan_cameras() -> None:
        # Opening each camera takes a moment, so do it off the tray's thread.
        def work() -> None:
            cameras["scanning"] = True
            icon.update_menu()
            try:
                cameras["found"] = find_cameras(current=engine.camera)
            finally:
                cameras["scanning"] = False
                icon.update_menu()

        threading.Thread(target=work, daemon=True).start()

    def camera_items():
        for i in cameras["found"]:
            yield pystray.MenuItem(
                f"Camera {i}",
                lambda _icon, _item, i=i: engine.switch_camera(i),
                checked=lambda _item, i=i: engine.camera == i,
                radio=True,
            )
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem(
            lambda _: "Looking for cameras..." if cameras["scanning"] else "Look for cameras again",
            lambda _icon, _item: scan_cameras(),
            enabled=lambda _: not cameras["scanning"],
        )

    def reminder_items():
        for key, label in REMINDER_LABELS.items():
            yield pystray.MenuItem(
                label,
                lambda _i, _m, key=key: engine.set_reminder(
                    key, not getattr(engine.settings.reminders, key)
                ),
                checked=lambda _m, key=key: getattr(engine.settings.reminders, key),
            )

    def sensitivity_items():
        for name in SENSITIVITY:
            yield pystray.MenuItem(
                name.capitalize(),
                lambda _i, _m, name=name: engine.set_sensitivity(name),
                checked=lambda _m, name=name: engine.settings.sensitivity == name,
                radio=True,
            )

    def pause_for(minutes: int):
        return lambda _icon, _item: engine.pause_for(minutes * 60)

    def calibrate(_icon, _item) -> None:
        engine.resume()
        engine.request_calibration()

    def quit_app(_icon, _item) -> None:
        engine.stop()
        icon.stop()

    run_key = autostart.RunKey()

    def toggle_autostart(_icon, _item) -> None:
        try:
            autostart.set_enabled(run_key, not autostart.is_enabled(run_key))
        except OSError as exc:
            notify(icon, "SitRight", f"Couldn't change the startup setting: {exc}")

    icon = pystray.Icon(
        "sitright",
        make_icon("idle"),
        "SitRight",
        menu=pystray.Menu(
            pystray.MenuItem(lambda _: engine.stats_summary(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda _: f"Resume ({engine.pause_text()})",
                lambda _icon, _item: engine.resume(),
                visible=lambda _: engine.paused,
            ),
            pystray.MenuItem(
                "Pause",
                pystray.Menu(
                    pystray.MenuItem("30 minutes", pause_for(30)),
                    pystray.MenuItem("1 hour", pause_for(60)),
                    pystray.MenuItem("Until tomorrow", lambda _i, _m: engine.pause_until_tomorrow()),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem(
                        "Pause during calls",
                        lambda _i, _m: engine.set_auto_pause(not engine.settings.auto_pause),
                        checked=lambda _: engine.settings.auto_pause,
                    ),
                ),
            ),
            pystray.MenuItem(
                "Snooze 15 min",
                lambda _icon, _item: engine.unsnooze() if engine.snoozed else engine.snooze(),
                checked=lambda _: engine.snoozed,
            ),
            pystray.MenuItem("Reminders", pystray.Menu(*reminder_items())),
            pystray.MenuItem("Sensitivity", pystray.Menu(*sensitivity_items())),
            pystray.MenuItem("Calibrate (sit straight first)", calibrate),
            pystray.MenuItem("Camera", pystray.Menu(camera_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start with Windows",
                toggle_autostart,
                checked=lambda _: autostart.is_enabled(run_key),
                visible=sys.platform == "win32",
            ),
            pystray.MenuItem("Quit", quit_app),
        ),
    )

    def setup(icon) -> None:
        icon.visible = True
        if problems:
            more = f" (+{len(problems) - 2} more)" if len(problems) > 2 else ""
            notify(icon, "SitRight: check config.json", " ".join(problems[:2]) + more)
        engine.start()
        scan_cameras()

    icon.run(setup)


def draw_lines(frame, lines: list[tuple[str, tuple[int, int, int]]], scale: float = 0.8) -> None:
    import cv2

    step = int(40 * scale)
    for i, (text, color) in enumerate(lines):
        org = (16, step + i * step)
        cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA)


def draw_countdown(frame, number: str) -> None:
    import cv2

    h, w = frame.shape[:2]
    font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, h / 90, max(4, h // 40)
    (tw, th), _ = cv2.getTextSize(number, font, scale, thick)
    org = ((w - tw) // 2, (h + th) // 2)
    cv2.putText(frame, number, org, font, scale, (0, 0, 0), thick + 6, cv2.LINE_AA)
    cv2.putText(frame, number, org, font, scale, (255, 255, 255), thick, cv2.LINE_AA)


class CvCalibrationView:
    """A small always-on-top window showing the camera, the countdown and hints."""

    TITLE = "SitRight calibration (Esc = cancel)"
    SIZE = (480, 360)

    def __init__(self) -> None:
        self._shown = False

    def show(self, frame, lines: list[str]) -> bool:
        import cv2

        if frame is None:
            return True
        img = cv2.resize(cv2.flip(frame, 1), self.SIZE)  # mirrored, like a selfie camera
        text = lines
        if lines and lines[0].isdigit():
            draw_countdown(img, lines[0])
            text = lines[1:]
        draw_lines(img, [(t, (0, 200, 255) if i else (255, 255, 255)) for i, t in enumerate(text)],
                   scale=0.6)
        cv2.imshow(self.TITLE, img)
        if not self._shown:
            self._shown = True
            cv2.setWindowProperty(self.TITLE, cv2.WND_PROP_TOPMOST, 1)
        if cv2.waitKey(1) & 0xFF == 27:
            return False
        # The user closed the window with its X button.
        return cv2.getWindowProperty(self.TITLE, cv2.WND_PROP_VISIBLE) >= 1

    def close(self) -> None:
        import cv2

        if self._shown:
            cv2.destroyWindow(self.TITLE)
            cv2.waitKey(1)


def run_preview(camera: int | None = None) -> None:
    """Camera window with live numbers. Handy for tuning and for recording a demo."""
    import cv2

    settings, problems = config.read()
    for problem in problems:
        print("config.json:", problem)
    try:
        detector = open_detector(settings.camera if camera is None else camera)
    except CameraError as exc:
        print(exc)
        return

    def new_monitor(baseline: Baseline | None) -> Monitor | None:
        thresholds = settings.thresholds.scaled(settings.sensitivity)
        return Monitor(baseline, thresholds, settings.reminders) if baseline else None

    monitor = new_monitor(settings.baseline)
    calib: list[Sample] = []
    calib_start: float | None = None
    warnings: list[str] = []
    try:
        while True:
            now = time.monotonic()
            try:
                sample = detector.read(now)
            except CameraError as exc:
                print(exc)
                break
            frame = cv2.flip(detector.last_frame, 1)
            lines: list[tuple[str, tuple[int, int, int]]] = []
            phase = "idle"
            if calib_start is not None:
                phase, count = calibration_phase(now - calib_start, COUNTDOWN, CALIBRATION_SECONDS)
                if phase == "countdown":
                    draw_countdown(frame, str(count))
                    hints = pose_warnings([sample]) if sample else ["I can't see your face yet."]
                    lines += [(h, (0, 200, 255)) for h in hints]
                elif phase == "capture":
                    if sample:
                        calib.append(sample)
                    lines.append(("Hold still...", (0, 200, 255)))
                else:
                    try:
                        settings.baseline = Baseline.from_samples(calib)
                        config.save(settings)
                        monitor = new_monitor(settings.baseline)
                        warnings = pose_warnings(calib)
                    except ValueError:
                        warnings = ["I couldn't see your face. Press C to try again."]
                    calib, calib_start = [], None
            if phase in ("idle", "done"):
                if monitor is None:
                    lines.append(("Press C to calibrate", (0, 200, 255)))
                else:
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
                lines += [(w, (0, 200, 255)) for w in warnings]
            draw_lines(frame, lines)
            cv2.imshow("SitRight preview (C = calibrate, Q = quit)", frame)
            key = cv2.waitKey(max(1, int(1000 / FPS))) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("c"):
                calib, calib_start, warnings = [], time.monotonic(), []
    finally:
        detector.close()
        cv2.destroyAllWindows()
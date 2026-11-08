"""Tray app: a background thread watches the camera, the tray icon shows the result."""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from pathlib import Path

from . import autostart, config, inuse, stats
from .icons import make_icon
from .screen import Cell, Screen, dark_mode, fit_picture, render, to_bgr
from .engine import (
    CALIBRATION_SECONDS,
    COUNTDOWN,
    FPS,
    MAX_READ_FAILURES,
    CameraError,
    Engine,
    ModelError,
    find_cameras,
    open_detector,
)
from .monitor import (
    SENSITIVITY,
    Baseline,
    Monitor,
    Sample,
    Status,
    calibration_phase,
    pose_warnings,
)

REMINDER_LABELS = {
    "slouch": "Sit up straight",
    "too_close": "Lean back",
    "blink": "Blink (experimental)",
    "take_break": "Take a break",
}
SYSTEM_DPI_AWARE = 1
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
WM_SETICON, ICON_SMALL, ICON_BIG = 0x80, 0, 1
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
WINDOW_ICON = Path(__file__).with_name("assets") / "sitright.ico"  # written by tools/make_icons.py
PREVIEW_TITLE = "SitRight preview (C = calibrate, Q = quit)"
MAX_NOTIFY_TITLE = 63
MAX_NOTIFY_TEXT = 255


def shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def notify(icon, title: str, message: str) -> None:
    """Show a notification. A failure here must never take the caller down."""
    # pystray copies these into fixed Windows buffers and raises if they don't fit.
    title, message = shorten(title, MAX_NOTIFY_TITLE), shorten(message, MAX_NOTIFY_TEXT)
    try:
        if icon.HAS_NOTIFICATION:
            icon.notify(message, title)
            return
        from plyer import notification

        notification.notify(title=title, message=message, app_name="SitRight", timeout=8)
    except Exception:
        print(f"{title}: {message}")


def finish(engine: Engine, timeout: float = 3.0) -> None:
    """Give the engine thread time to close the camera and save today's numbers.

    It's a daemon thread, so Python would otherwise cut it off at exit.
    """
    engine.stop()
    if engine.is_alive():
        engine.join(timeout)


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
        # Remember a value only once it's written, so a failed write is tried again.
        if state != self.state:
            icon.icon = make_icon(state)
            self.state = state
        if tooltip != self.title:
            icon.title = tooltip[:127]  # Windows cuts tray tooltips at 127 characters
            self.title = tooltip


def run_tray(camera: int | None = None) -> None:
    """`camera` overrides the saved choice for this run only."""
    import pystray

    enable_high_dpi()
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
        check_locked=inuse.screen_locked,
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

    def action(fn, *args):
        # pystray rejects actions with more than (icon, item) parameters, so no
        # `lambda icon, item, i=i:` tricks; bind the arguments in a closure instead.
        return lambda _icon, _item: fn(*args)

    def camera_items():
        for i in cameras["found"]:
            yield pystray.MenuItem(
                f"Camera {i}",
                action(engine.switch_camera, i),
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
                action(lambda key: engine.set_reminder(
                    key, not getattr(engine.settings.reminders, key)
                ), key),
                checked=lambda _m, key=key: getattr(engine.settings.reminders, key),
            )

    def sensitivity_items():
        for name in SENSITIVITY:
            yield pystray.MenuItem(
                name.capitalize(),
                action(engine.set_sensitivity, name),
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

    try:
        icon.run(setup)
    finally:
        finish(engine)


def enable_high_dpi() -> None:
    """Sharp menus and tray icon on scaled displays. Call before any window exists."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(SYSTEM_DPI_AWARE)
    except (OSError, AttributeError):
        pass  # already set, or an older Windows that scales the windows itself


def display_scale() -> float:
    """1.0 at 100% display scaling. Our cv2 windows are sized by hand, so they need this."""
    if sys.platform != "win32":
        return 1.0
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96
    except (OSError, AttributeError):
        return 1.0


def style_window(title: str, dark: bool) -> None:
    """Give one of our cv2 windows the SitRight icon, and a dark title bar in dark mode."""
    if sys.platform != "win32":
        return
    from ctypes import wintypes

    try:
        user32 = ctypes.WinDLL("user32")
        user32.FindWindowW.restype = wintypes.HWND
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return
        for which, size in ((ICON_SMALL, 16), (ICON_BIG, 32)):
            px = round(size * display_scale())
            icon = user32.LoadImageW(None, str(WINDOW_ICON), IMAGE_ICON, px, px, LR_LOADFROMFILE)
            if icon:
                user32.SendMessageW(hwnd, WM_SETICON, which, icon)
        if dark:
            on = ctypes.c_int(1)
            ctypes.WinDLL("dwmapi").DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(on), ctypes.sizeof(on)
            )
    except (OSError, AttributeError):
        pass  # an older Windows keeps the plain window look


def calibration_screen(lines: list[str]) -> Screen:
    """The engine's calibration lines: a countdown number or "Hold still", then hints."""
    if not lines:
        return Screen()
    counting = lines[0].isdigit()
    return Screen(
        lamp="idle" if counting else "live",
        reading=lines[0],
        label=lines[1] if len(lines) > 1 else "",
        notes=lines[2:],
    )


def center_window(title: str, width: int, height: int) -> None:
    import cv2

    if sys.platform == "win32":  # other desktops place new windows sensibly by themselves
        metrics = ctypes.windll.user32.GetSystemMetrics
        cv2.moveWindow(title, max(0, (metrics(0) - width) // 2), max(0, (metrics(1) - height) // 2))


def monitor_screen(s: Status) -> Screen:
    """The preview's panel while it compares frames with the calibrated pose."""
    if not s.face:
        lamp, reading = "idle", "No face in view"
    elif s.slouching or s.too_close:
        lamp = "warn"
        reading = " and ".join(p for p, bad in (("Slouching", s.slouching), ("too close", s.too_close)) if bad)
        reading = reading[0].upper() + reading[1:]
    else:
        lamp, reading = "ok", "Sitting well"
    bpm = "Measuring" if s.blinks_per_minute is None else f"{s.blinks_per_minute:.0f}/min"
    return Screen(
        lamp=lamp,
        reading=reading,
        label="Press C to calibrate again, Q to quit",
        cells=[
            Cell("Slouching" if s.slouching else "Good", "Posture"),
            Cell("Too close" if s.too_close else "Good", "Distance"),
            Cell(bpm, "Blinks"),
        ],
    )


class CvCalibrationView:
    """A small always-on-top window showing the camera, the countdown and hints."""

    TITLE = "SitRight calibration (Esc = cancel)"
    SIZE = (480, 360)  # camera picture box at 100% display scaling

    def __init__(self) -> None:
        self._shown = False
        self._scale = display_scale()
        self._dark = dark_mode()

    def compose(self, frame, lines: list[str]):
        """The window's image: the camera mirrored like a selfie, the status panel under it."""
        box = (round(self.SIZE[0] * self._scale), round(self.SIZE[1] * self._scale))
        img = render(fit_picture(frame, box), calibration_screen(lines), self._scale, self._dark)
        return to_bgr(img)

    def show(self, frame, lines: list[str]) -> bool:
        import cv2

        if frame is None:
            return True
        img = self.compose(frame, lines)
        cv2.imshow(self.TITLE, img)
        if not self._shown:
            self._shown = True
            cv2.setWindowProperty(self.TITLE, cv2.WND_PROP_TOPMOST, 1)
            style_window(self.TITLE, self._dark)
            center_window(self.TITLE, img.shape[1], img.shape[0])
        if cv2.waitKey(1) & 0xFF == 27:
            return False
        # The user closed the window with its X button.
        return cv2.getWindowProperty(self.TITLE, cv2.WND_PROP_VISIBLE) >= 1

    def close(self) -> None:
        import cv2

        if self._shown:
            try:
                cv2.destroyWindow(self.TITLE)
            except cv2.error:
                pass  # the user already closed it with its X button
            cv2.waitKey(1)


def run_preview(camera: int | None = None) -> None:
    """Camera window with live numbers. Handy for tuning and for recording a demo."""
    import cv2

    enable_high_dpi()
    scale = display_scale()
    dark = dark_mode()
    settings, problems = config.read()
    for problem in problems:
        print("config.json:", problem)
    try:
        detector = open_detector(settings.camera if camera is None else camera)
    except (CameraError, ModelError) as exc:
        print(exc)
        return

    def new_monitor(baseline: Baseline | None) -> Monitor | None:
        thresholds = settings.thresholds.scaled(settings.sensitivity)
        return Monitor(baseline, thresholds, settings.reminders) if baseline else None

    monitor = new_monitor(settings.baseline)
    calib: list[Sample] = []
    calib_start: float | None = None
    warnings: list[str] = []
    failures = 0
    shown = False
    try:
        while True:
            now = time.monotonic()
            try:
                sample = detector.read(now)
            except CameraError as exc:
                failures += 1  # one dropped frame shouldn't end the preview
                if failures >= MAX_READ_FAILURES:
                    print(exc)
                    break
                time.sleep(1 / FPS)
                continue
            failures = 0
            frame = detector.last_frame
            h, w = frame.shape[:2]
            picture = fit_picture(frame, (round(w * scale), round(h * scale)))
            screen = Screen()
            phase = "idle"
            if calib_start is not None:
                phase, count = calibration_phase(now - calib_start, COUNTDOWN, CALIBRATION_SECONDS)
                if phase == "countdown":
                    hints = pose_warnings([sample]) if sample else ["I can't see your face yet."]
                    screen = Screen("idle", str(count), "Sit the way you want to sit", notes=hints)
                elif phase == "capture":
                    if sample:
                        calib.append(sample)
                    screen = Screen("live", "Hold still", "Look at the screen")
                else:
                    try:
                        settings.baseline = Baseline.from_samples(calib)
                        monitor = new_monitor(settings.baseline)
                        warnings = pose_warnings(calib)
                        config.save(settings)
                    except ValueError:
                        warnings = ["I couldn't see your face. Press C to try again."]
                    except OSError as exc:  # the pose still works until the preview closes
                        warnings.append(f"Couldn't save the pose: {exc}")
                    calib, calib_start = [], None
            if phase in ("idle", "done"):
                if monitor is None:
                    screen = Screen("idle", "Not calibrated", "Press C to calibrate, Q to quit")
                else:
                    alerts = monitor.update(sample, now)
                    screen = monitor_screen(monitor.status)
                    for alert in alerts:
                        print(alert.value)
                screen.notes += warnings
            img = to_bgr(render(picture, screen, scale, dark))
            cv2.imshow(PREVIEW_TITLE, img)
            if not shown:
                shown = True
                style_window(PREVIEW_TITLE, dark)
            key = cv2.waitKey(max(1, int(1000 / FPS))) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                break
            if cv2.getWindowProperty(PREVIEW_TITLE, cv2.WND_PROP_VISIBLE) < 1:
                break  # its X button; the next imshow would bring the window straight back
            if key in (ord("c"), ord("C")):
                calib, calib_start, warnings = [], time.monotonic(), []
    finally:
        detector.close()
        cv2.destroyAllWindows()

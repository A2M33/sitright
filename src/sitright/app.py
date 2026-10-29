"""Tray app: a background thread watches the camera, the tray icon shows the result."""

from __future__ import annotations

import sys
import threading
import time

from PIL import Image, ImageDraw

from . import autostart, config, inuse
from .engine import (
    CALIBRATION_SECONDS,
    FPS,
    CameraError,
    Engine,
    find_cameras,
    open_detector,
)
from .monitor import SENSITIVITY, Baseline, Monitor, Sample

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


def run_tray(camera: int | None = None) -> None:
    """`camera` overrides the saved choice for this run only."""
    import pystray

    state = {"current": None}

    def on_change(new_state: str, tooltip: str) -> None:
        if state["current"] != new_state:
            state["current"] = new_state
            icon.icon = make_icon(new_state)
        icon.title = tooltip

    settings = config.load()
    engine = Engine(
        settings.camera if camera is None else camera,
        on_change,
        lambda t, m: notify(icon, t, m),
        settings,
        check_in_use=inuse.others_using_camera_or_mic,
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
        engine.start()
        scan_cameras()

    icon.run(setup)


def run_preview(camera: int | None = None) -> None:
    """Camera window with live numbers. Handy for tuning and for recording a demo."""
    import cv2

    settings = config.load()
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
    calib_until = 0.0
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
            if now < calib_until:
                if sample:
                    calib.append(sample)
                lines.append(("Calibrating... sit straight", (0, 200, 255)))
            elif calib:
                try:
                    settings.baseline = Baseline.from_samples(calib)
                    config.save(settings)
                    monitor = new_monitor(settings.baseline)
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

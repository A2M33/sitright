import sys
import threading
import time

import pytest

from sitright.app import TrayUpdater, finish, fit_size, notify, wrap_text


class FakeIcon:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def __setattr__(self, name, value):
        if name in ("icon", "title"):
            self.writes.append(name)
        super().__setattr__(name, value)


def test_tray_is_only_touched_when_something_changed():
    icon = FakeIcon()
    tray = TrayUpdater(lambda: icon)
    for _ in range(20):  # one second of identical frames
        tray.update("ok", "SitRight: sitting 3 min")
    assert icon.writes == ["icon", "title"]
    tray.update("ok", "SitRight: sitting 4 min")
    assert icon.writes == ["icon", "title", "title"]
    tray.update("warn", "SitRight: sitting 4 min")
    assert icon.writes == ["icon", "title", "title", "icon"]


def test_long_tooltips_are_cut_to_the_windows_limit():
    icon = FakeIcon()
    TrayUpdater(lambda: icon).update("idle", "x" * 300)
    assert len(icon.title) == 127


@pytest.mark.skipif(sys.platform != "win32", reason="pystray needs a desktop session")
def test_tray_menu_builds_and_its_items_work(tmp_path, monkeypatch):
    pystray = pytest.importorskip("pystray")
    from sitright import app

    monkeypatch.setenv("APPDATA", str(tmp_path))
    seen = {}

    def fake_run(icon, setup=None):  # build the menu, don't start the loop or the camera
        icon.update_menu()
        items = {str(i.text): i for i in icon.menu.items if i is not pystray.Menu.SEPARATOR}
        sens = items["Sensitivity"].submenu.items
        sens[2](icon)  # click "Strict"
        rem = items["Reminders"].submenu.items
        rem[0](icon)  # untick "Sit up straight"
        seen["strict"] = sens[2].checked
        seen["slouch"] = rem[0].checked
        seen["names"] = list(items)

    monkeypatch.setattr(pystray.Icon, "run", fake_run)
    app.run_tray(camera=0)
    assert seen["strict"] is True and seen["slouch"] is False
    assert {"Pause", "Reminders", "Sensitivity", "Camera", "Snooze 15 min", "Quit"} <= set(seen["names"])
    saved = (tmp_path / "sitright" / "config.json").read_text(encoding="utf-8")
    assert '"sensitivity": "strict"' in saved

def test_closing_a_window_the_user_already_closed_is_not_an_error(monkeypatch):
    cv2 = pytest.importorskip("cv2")
    from sitright.app import CvCalibrationView

    def gone(_title):
        raise cv2.error("NULL window")

    monkeypatch.setattr(cv2, "destroyWindow", gone)
    monkeypatch.setattr(cv2, "waitKey", lambda _ms: -1)
    view = CvCalibrationView()
    view._shown = True
    view.close()  # used to raise, which the engine then reported as a camera failure


class StrictIcon:
    """Fails like pystray does when a notification doesn't fit its Windows buffer."""

    HAS_NOTIFICATION = True

    def __init__(self) -> None:
        self.shown: list[tuple[str, str]] = []

    def notify(self, message, title):
        if len(message) > 255 or len(title) > 63:
            raise ValueError("string too long")
        self.shown.append((title, message))


def test_long_notifications_are_shortened_to_fit():
    icon = StrictIcon()
    notify(icon, "t" * 100, "m" * 400)
    title, message = icon.shown[0]
    assert len(title) == 63 and len(message) == 255
    assert message.endswith("...")


def test_a_failing_notification_does_not_reach_the_caller():
    class Broken(StrictIcon):
        def notify(self, message, title):
            raise OSError("tray is gone")

    notify(Broken(), "SitRight", "hello")


def test_a_failed_tray_write_is_tried_again():
    class Flaky(FakeIcon):
        def __setattr__(self, name, value):
            if name == "icon" and not self.__dict__.get("ready"):
                raise OSError("no temp file")
            super().__setattr__(name, value)

    icon = Flaky()
    tray = TrayUpdater(lambda: icon)
    with pytest.raises(OSError):
        tray.update("ok", "SitRight")
    icon.ready = True
    tray.update("ok", "SitRight")
    assert icon.writes == ["icon", "title"]


def test_finish_waits_for_the_camera_and_the_stats():
    from sitright.config import Settings
    from sitright.engine import Engine
    from sitright.monitor import Baseline, Sample
    from sitright.stats import StatsTracker

    class SlowDetector:
        closed = False

        def read(self, now):
            return Sample(ear=0.3, nose_y=0.5, face_width=0.3)

        def close(self):
            time.sleep(0.2)
            self.closed = True

    detector = SlowDetector()
    saved = []
    opened = threading.Event()

    def open_detector(_camera):
        opened.set()
        return detector

    engine = Engine(
        0,
        lambda state, tooltip: None,
        lambda title, message: None,
        settings=Settings(baseline=Baseline(0.5, 0.3, 0.3)),
        open_detector=open_detector,
        stats=StatsTracker(120, save=saved.append),
    )
    engine.start()
    assert opened.wait(5)
    time.sleep(0.1)
    saved.clear()
    finish(engine)
    assert detector.closed and saved


def test_finish_copes_with_an_engine_that_never_started():
    from sitright.engine import Engine

    finish(Engine(0, lambda s, t: None, lambda t, m: None))


@pytest.mark.skipif(sys.platform != "win32", reason="pystray needs a desktop session")
def test_quitting_the_tray_waits_for_the_engine(tmp_path, monkeypatch):
    pystray = pytest.importorskip("pystray")
    from sitright import app

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(pystray.Icon, "run", lambda icon, setup=None: None)
    finished = []
    monkeypatch.setattr(app, "finish", finished.append)
    app.run_tray(camera=0)
    assert len(finished) == 1


class PreviewDetector:
    """Plays `script` frame by frame: None for "no face", or an exception to raise."""

    def __init__(self, script) -> None:
        self.script = list(script)
        self.closed = False
        self.last_frame = None

    def read(self, now):
        import numpy

        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        self.last_frame = numpy.zeros((48, 64, 3), numpy.uint8)
        return step

    def close(self):
        self.closed = True


@pytest.fixture
def fake_window(tmp_path, monkeypatch):
    """cv2's window calls with no window: `shown` counts frames, `open_for` ends the run."""
    cv2 = pytest.importorskip("cv2")
    from sitright import app

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    state = {"shown": 0, "open_for": 1000, "keys": []}

    def imshow(_title, _frame):
        state["shown"] += 1

    def visible(_title, _prop):
        return 1 if state["shown"] < state["open_for"] else 0

    monkeypatch.setattr(cv2, "imshow", imshow)
    monkeypatch.setattr(cv2, "waitKey", lambda _ms: state["keys"].pop(0) if state["keys"] else -1)
    monkeypatch.setattr(cv2, "getWindowProperty", visible)
    monkeypatch.setattr(cv2, "destroyAllWindows", lambda: None)
    monkeypatch.setattr(app.time, "sleep", lambda _s: None)
    return state


def test_preview_ends_when_its_window_is_closed(fake_window, monkeypatch):
    from sitright import app

    detector = PreviewDetector([None] * 50)
    monkeypatch.setattr(app, "open_detector", lambda camera: detector)
    fake_window["open_for"] = 3
    app.run_preview(camera=0)  # used to reopen the window for ever
    assert fake_window["shown"] == 3
    assert detector.closed


def test_preview_survives_a_dropped_frame(fake_window, monkeypatch):
    from sitright import app
    from sitright.engine import CameraError

    detector = PreviewDetector([None, CameraError("glitch"), None])
    monkeypatch.setattr(app, "open_detector", lambda camera: detector)
    fake_window["keys"] = [-1, ord("Q")]  # capital Q works with Caps Lock on
    app.run_preview(camera=0)
    assert fake_window["shown"] == 2


def test_preview_stops_on_a_dead_camera(fake_window, monkeypatch, capsys):
    from sitright import app
    from sitright.engine import MAX_READ_FAILURES, CameraError

    detector = PreviewDetector([CameraError("gone")] * MAX_READ_FAILURES)
    monkeypatch.setattr(app, "open_detector", lambda camera: detector)
    app.run_preview(camera=0)
    assert "gone" in capsys.readouterr().out
    assert detector.closed


def test_preview_explains_a_missing_model(fake_window, monkeypatch, capsys):
    from sitright import app
    from sitright.engine import ModelError

    def no_model(_camera):
        raise ModelError("Couldn't download the face model.")

    monkeypatch.setattr(app, "open_detector", no_model)
    app.run_preview(camera=0)
    assert "face model" in capsys.readouterr().out


def test_wrap_text_breaks_at_spaces():
    def measure(text):
        return len(text)

    assert wrap_text("aa bb cc dd", 5, measure) == ["aa bb", "cc dd"]
    assert wrap_text("aa", 5, measure) == ["aa"]
    assert wrap_text("", 5, measure) == []
    assert wrap_text("averyveryverylongword bb", 5, measure) == ["averyveryverylongword", "bb"]


def test_fit_size_keeps_the_proportions():
    assert fit_size(640, 480, (480, 360)) == (480, 360)
    assert fit_size(640, 360, (480, 360)) == (480, 270)  # a 16:9 camera isn't stretched to 4:3
    assert fit_size(480, 640, (480, 360)) == (270, 360)


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
def test_every_calibration_hint_fits_the_window(monkeypatch, scale):
    cv2 = pytest.importorskip("cv2")
    from sitright import app
    from sitright.monitor import Sample, pose_warnings

    numpy = pytest.importorskip("numpy")
    monkeypatch.setattr(app, "display_scale", lambda: scale)
    view = app.CvCalibrationView()
    bad_pose = Sample(ear=0.3, nose_y=0.9, face_width=0.8, pitch=40.0, nose_x=0.9)
    hints = pose_warnings([bad_pose])
    assert len(hints) == 3
    img = view.compose(numpy.zeros((480, 640, 3), numpy.uint8), ["3", "Sit up"] + hints[:1])
    assert img.shape[1] == round(480 * scale)
    text_scale = 0.6 * scale
    margin = round(20 * text_scale)
    wrapped = app.layout_lines([(h, app.AMBER) for h in hints], text_scale, img.shape[1] - 2 * margin)
    assert len(wrapped) > len(hints)  # these used to run off the right edge
    for text, _color in wrapped:
        width = cv2.getTextSize(
            text, cv2.FONT_HERSHEY_SIMPLEX, text_scale, app.text_weight(text_scale)
        )[0][0]
        assert width <= img.shape[1] - 2 * margin


def test_a_wide_camera_is_not_stretched_in_the_calibration_window(monkeypatch):
    pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")
    from sitright import app

    monkeypatch.setattr(app, "display_scale", lambda: 1.0)
    img = app.CvCalibrationView().compose(numpy.zeros((360, 640, 3), numpy.uint8), ["Hold still..."])
    assert img.shape[:2] == (270, 480)


def test_calibration_text_goes_on_a_band_at_the_bottom(monkeypatch):
    pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")
    from sitright import app

    monkeypatch.setattr(app, "display_scale", lambda: 1.0)
    frame = numpy.full((480, 640, 3), 200, numpy.uint8)
    img = app.CvCalibrationView().compose(frame, ["Hold still...", "Look at the screen"])
    assert (img[10, 240] == 200).all()  # the face area stays as it was
    assert img[-2, 2].max() < 100  # the band is dark so the text reads on any background

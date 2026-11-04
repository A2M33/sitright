import sys

import pytest

from sitright.app import TrayUpdater, notify


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

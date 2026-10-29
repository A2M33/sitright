import sys

import pytest

from sitright.app import TrayUpdater


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
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

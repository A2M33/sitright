from sitright import autostart


class FakeStore(dict):
    def set(self, name, value):
        self[name] = value

    def delete(self, name):
        self.pop(name, None)


VENV = r"C:\Users\me\sitright\.venv\Scripts\python.exe"


def test_command_prefers_pythonw_so_no_console_opens():
    cmd = autostart.command(VENV, exists=lambda p: True)
    assert cmd == r'"C:\Users\me\sitright\.venv\Scripts\pythonw.exe" -m sitright'


def test_command_falls_back_to_current_interpreter():
    assert autostart.command(VENV, exists=lambda p: False) == f'"{VENV}" -m sitright'


def test_toggle():
    store = FakeStore()
    assert not autostart.is_enabled(store)
    autostart.set_enabled(store, True, cmd='"x" -m sitright')
    assert autostart.is_enabled(store)
    assert store["SitRight"] == '"x" -m sitright'
    autostart.set_enabled(store, False)
    assert not autostart.is_enabled(store)
    autostart.set_enabled(store, False)  # turning off twice is fine

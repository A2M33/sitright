"""'Start with Windows': a value under HKCU\\...\\CurrentVersion\\Run.

The registry access sits behind a tiny get/set/delete interface so tests can use a dict.
"""

from __future__ import annotations

import ntpath  # the command line is a Windows path even when tests run elsewhere
import os
import sys
from typing import Callable, Protocol

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SitRight"


class Store(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


def command(executable: str = sys.executable, exists: Callable[[str], bool] = os.path.exists) -> str:
    """pythonw.exe -m sitright: starts the tray app without a console window."""
    pythonw = ntpath.join(ntpath.dirname(executable), "pythonw.exe")
    return f'"{pythonw if exists(pythonw) else executable}" -m sitright'


def is_enabled(store: Store) -> bool:
    return bool(store.get(VALUE_NAME))


def set_enabled(store: Store, enabled: bool, cmd: str | None = None) -> None:
    if enabled:
        store.set(VALUE_NAME, cmd or command())
    else:
        store.delete(VALUE_NAME)


class RunKey:
    """The real HKCU Run key."""

    def get(self, name: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                return winreg.QueryValueEx(key, name)[0]
        except OSError:
            return None

    def set(self, name: str, value: str) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass

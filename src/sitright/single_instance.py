"""One tray app at a time: two would fight over the camera and over config.json."""

from __future__ import annotations

import sys
from typing import Callable

ERROR_ALREADY_EXISTS = 183
MB_ICONINFORMATION = 0x40


def acquire(name: str = "tray") -> Callable[[], None] | None:
    """Claim `name` for this process. Returns a release function, or None if it's taken.

    The operating system drops the claim when the process ends, even after a crash.
    """
    return _mutex(f"Local\\SitRight-{name}") if sys.platform == "win32" else _lock_file(name)


def _mutex(name: str) -> Callable[[], None] | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return lambda: None  # we can't tell, and refusing to start would be worse
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return lambda: kernel32.CloseHandle(handle)


def _lock_file(name: str) -> Callable[[], None] | None:
    import fcntl

    from .config import cache_dir

    file = (cache_dir() / f"{name}.lock").open("w")
    try:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        file.close()
        return None
    return file.close


def tell_user(message: str) -> None:
    """Print to the terminal, or show a message box when there's no terminal to print to."""
    if sys.stdout is not None or sys.platform != "win32":
        print(message)
        return
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, message, "SitRight", MB_ICONINFORMATION)

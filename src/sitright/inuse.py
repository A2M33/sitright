"""Is another app using the camera or microphone right now? (Windows only)

Windows records every app that uses the camera or mic under
HKCU\\...\\CapabilityAccessManager\\ConsentStore\\{webcam,microphone}:
- Store apps get a subkey named after their package,
- desktop apps get a subkey under "NonPackaged", named after the exe path
  with "\\" replaced by "#".
Each has LastUsedTimeStart/LastUsedTimeStop. While the app holds the device,
LastUsedTimeStop is 0.

`apps_in_use` works on a plain dict snapshot of those keys, so it is tested
with fake registry data. `snapshot` is the thin winreg part.
"""

from __future__ import annotations

import ntpath  # registry paths are Windows paths, even when tests run elsewhere
import os
import sys
from typing import Iterable

CONSENT_STORE = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
DEVICES = ("webcam", "microphone")

# Noise filters and audio routers hold the mic (or camera) for as long as they run.
# Counting them would keep SitRight paused all day.
ALWAYS_ON = (
    "NVIDIA Broadcast.exe",
    "NVIDIA RTX Voice.exe",
    "Krisp.exe",
    "voicemeeter.exe",
    "voicemeeter_x64.exe",
    "voicemeeterpro.exe",
    "voicemeeterpro_x64.exe",
    "voicemeeter8.exe",
    "voicemeeter8x64.exe",
)

# A key snapshot: value names map to ints, subkey names map to nested dicts.
Key = dict


def _in_use(key: Key) -> bool:
    stop = key.get("LastUsedTimeStop")
    return isinstance(stop, int) and stop == 0


def _norm(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path))


def apps_in_use(store: Key, own_exes: Iterable[str] = (), ignore: Iterable[str] = ()) -> list[str]:
    """Names of apps currently using the device described by `store`.

    `own_exes`: our own interpreter paths, never reported.
    `ignore`: exe file names or package names the user wants to skip (case-insensitive).
    """
    own = {_norm(p) for p in own_exes}
    skip = {name.lower() for name in ignore}
    found = []
    for name, key in store.items():
        if not isinstance(key, dict):
            continue
        if name == "NonPackaged":
            for exe_key, app in key.items():
                if not isinstance(app, dict) or not _in_use(app):
                    continue
                path = exe_key.replace("#", "\\")
                exe = ntpath.basename(path)
                if _norm(path) in own or exe.lower() in skip:
                    continue
                found.append(exe)
        elif _in_use(key) and name.lower() not in skip:
            found.append(name.split("_")[0])  # "Microsoft.WindowsCamera_8wekyb3d8bbwe"
    return found


def own_executables() -> list[str]:
    """Paths Windows may record for this process.

    A venv's python.exe starts the base interpreter as a child, and that child is
    the process that opens the camera, so list both, console and windowed.
    """
    paths = set()
    for exe in (sys.executable, getattr(sys, "_base_executable", "")):
        if not exe:
            continue
        folder = os.path.dirname(exe)
        for name in ("python.exe", "pythonw.exe", os.path.basename(exe)):
            paths.add(os.path.join(folder, name))
            paths.add(os.path.realpath(os.path.join(folder, name)))
    return sorted(paths)


def snapshot(device: str) -> Key:
    """Read ConsentStore\\<device> into nested dicts. Empty dict if unavailable."""
    try:
        import winreg
    except ImportError:  # not Windows
        return {}

    def read(handle) -> Key:
        out: Key = {}
        i = 0
        while True:
            try:
                name, value, _kind = winreg.EnumValue(handle, i)
            except OSError:
                break
            out[name] = value
            i += 1
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(handle, i)
            except OSError:
                break
            try:
                with winreg.OpenKey(handle, sub) as child:
                    out[sub] = read(child)
            except OSError:
                pass
            i += 1
        return out

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{CONSENT_STORE}\{device}") as key:
            return read(key)
    except OSError:
        return {}


def others_using_camera_or_mic(ignore: Iterable[str] = ALWAYS_ON) -> list[str]:
    """Apps other than SitRight that hold the camera or microphone right now."""
    own = own_executables()
    found: list[str] = []
    for device in DEVICES:
        for app in apps_in_use(snapshot(device), own, ignore):
            if app not in found:
                found.append(app)
    return found

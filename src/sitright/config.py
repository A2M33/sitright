"""Where settings and the calibrated pose live on disk.

config.json is meant to be hand-edited, so reading it never crashes: unknown keys
are skipped and a bad value falls back to its default with a warning. Saving
merges into the file and leaves `thresholds` as the user wrote them.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sys
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .monitor import SENSITIVITY, Baseline, Reminders, Thresholds

APP = "sitright"

_save_lock = threading.Lock()  # the tray and the engine thread both save

# Zero here would divide by zero or reset the timers on every frame; the rest may be zero.
MUST_BE_POSITIVE = (
    "too_close_ratio",
    "blink_window_seconds",
    "break_after_minutes",
    "away_reset_seconds",
)


@dataclass
class Settings:
    baseline: Baseline | None = None
    thresholds: Thresholds = field(default_factory=Thresholds)
    reminders: Reminders = field(default_factory=Reminders)
    camera: int = 0  # OpenCV camera index
    sensitivity: str = "normal"  # relaxed / normal / strict, scales the thresholds
    auto_pause: bool = True  # pause while another app uses the camera or mic


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = base / APP
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    path = base / APP
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file() -> Path:
    return config_dir() / "config.json"


def write_atomic(path: Path, text: str) -> None:
    """Replace `path` in one step, so a crash or power cut can't leave half a file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(3):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # a virus scanner or editor has the file open for a moment
            if attempt == 2:
                raise
            time.sleep(0.1)


def _keep_damaged_copy() -> None:
    try:
        shutil.copyfile(_file(), _file().with_name("config.json.bad"))
    except OSError:
        pass


def _finite(value: float) -> bool:
    """JSON lets NaN, Infinity and 400-digit integers through."""
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _valid(value, kind: str) -> bool:
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind in ("float", "float | None"):
        if value is None:
            return kind == "float | None"
        return isinstance(value, (int, float)) and not isinstance(value, bool) and _finite(value)
    if kind == "str":
        return isinstance(value, str)
    return False


def _section(cls, raw, name: str, problems: list[str]) -> dict:
    """Keyword arguments for `cls` from `raw`, keeping only well-typed known fields."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        problems.append(f'"{name}" should be a {{...}} block; using defaults.')
        return {}
    kinds = {f.name: f.type for f in fields(cls)}
    out = {}
    for key, value in raw.items():
        if key not in kinds:
            problems.append(f'Unknown setting "{name}.{key}" ignored.')
        elif _valid(value, kinds[key]):
            out[key] = float(value) if kinds[key] == "float" else value
        else:
            problems.append(f'"{name}.{key}" has the wrong type ({value!r}); using the default.')
    return out


def _in_range(values: dict, problems: list[str]) -> dict:
    """Threshold values with the ones that can't work taken out."""
    out = {}
    for key, value in values.items():
        if value < 0 or (value == 0 and key in MUST_BE_POSITIVE):
            problems.append(f'"thresholds.{key}" can\'t be {value:g}; using the default.')
        else:
            out[key] = value
    return out


def parse(data) -> tuple[Settings, list[str]]:
    """Settings from decoded JSON, plus human-readable problems found on the way."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return Settings(), ["config.json should hold a {...} object; using defaults."]
    s = Settings()

    raw_baseline = data.get("baseline")
    if raw_baseline is not None:
        values = _section(Baseline, raw_baseline, "baseline", problems)
        try:
            s.baseline = Baseline(**values)
        except TypeError:
            problems.append("The saved pose is incomplete. Calibrate again from the tray menu.")
        if s.baseline and (s.baseline.face_width <= 0 or s.baseline.open_ear <= 0):
            s.baseline = None
            problems.append("The saved pose makes no sense. Calibrate again from the tray menu.")
    raw_thresholds = _section(Thresholds, data.get("thresholds"), "thresholds", problems)
    s.thresholds = Thresholds(**_in_range(raw_thresholds, problems))
    s.reminders = Reminders(**_section(Reminders, data.get("reminders"), "reminders", problems))

    for key, kind in (("camera", "int"), ("sensitivity", "str"), ("auto_pause", "bool")):
        if key not in data:
            continue
        if not _valid(data[key], kind):
            problems.append(f'"{key}" has the wrong type ({data[key]!r}); using the default.')
            continue
        setattr(s, key, data[key])
    if s.sensitivity not in SENSITIVITY:
        problems.append(f'"sensitivity" must be one of {", ".join(SENSITIVITY)}; using normal.')
        s.sensitivity = "normal"
    if s.camera < 0:
        problems.append('"camera" can\'t be negative; using camera 0.')
        s.camera = 0

    known = {"baseline", "thresholds", "reminders", "camera", "sensitivity", "auto_pause"}
    problems += [f'Unknown setting "{key}" ignored.' for key in data if key not in known]
    return s, problems


def _read_raw():
    return json.loads(_file().read_text(encoding="utf-8"))


def read() -> tuple[Settings, list[str]]:
    try:
        data = _read_raw()
    except FileNotFoundError:
        return Settings(), []
    except (ValueError, OSError) as exc:
        return Settings(), [f"Couldn't read config.json ({exc}); using defaults."]
    return parse(data)


def load() -> Settings:
    return read()[0]


def save(settings: Settings) -> None:
    """Write what the app manages; leave hand-edited thresholds and unknown keys alone.

    A file we can't parse is copied to config.json.bad first, so a typo doesn't cost the
    user their tuning. Raises OSError if the file can't be read or written.
    """
    with _save_lock:
        _save(settings)


def _save(settings: Settings) -> None:
    try:
        data = _read_raw()
    except FileNotFoundError:
        data = {}
    except ValueError:
        _keep_damaged_copy()
        data = {}
    if not isinstance(data, dict):
        _keep_damaged_copy()
        data = {}
    data["baseline"] = asdict(settings.baseline) if settings.baseline else None
    data["reminders"] = asdict(settings.reminders)
    data["camera"] = settings.camera
    data["sensitivity"] = settings.sensitivity
    data["auto_pause"] = settings.auto_pause
    # The app never changes thresholds. Re-read them from the file so edits made while
    # it runs survive; only fill in keys that are missing, so they're easy to find.
    thresholds = data.get("thresholds")
    if not isinstance(thresholds, dict):
        thresholds = {}
    for key, value in asdict(settings.thresholds).items():
        thresholds.setdefault(key, value)
    data["thresholds"] = thresholds
    write_atomic(_file(), json.dumps(data, indent=2))

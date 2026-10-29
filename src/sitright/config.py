"""Where settings and the calibrated pose live on disk."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .monitor import Baseline, Reminders, Thresholds

APP = "sitright"


@dataclass
class Settings:
    baseline: Baseline | None = None
    thresholds: Thresholds = field(default_factory=Thresholds)
    reminders: Reminders = field(default_factory=Reminders)
    camera: int = 0  # OpenCV camera index


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


def load() -> Settings:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return Settings()
    baseline = Baseline(**data["baseline"]) if data.get("baseline") else None
    known = Thresholds.__dataclass_fields__
    thresholds = Thresholds(**{k: v for k, v in data.get("thresholds", {}).items() if k in known})
    known = Reminders.__dataclass_fields__
    reminders = Reminders(**{k: v for k, v in data.get("reminders", {}).items() if k in known})
    return Settings(baseline, thresholds, reminders, data.get("camera", 0))


def save(settings: Settings) -> None:
    data = {
        "baseline": asdict(settings.baseline) if settings.baseline else None,
        "thresholds": asdict(settings.thresholds),
        "reminders": asdict(settings.reminders),
        "camera": settings.camera,
    }
    _file().write_text(json.dumps(data, indent=2), encoding="utf-8")

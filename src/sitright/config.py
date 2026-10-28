"""Where settings and the calibrated pose live on disk."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from .monitor import Baseline, Thresholds

APP = "sitright"


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


def load() -> tuple[Baseline | None, Thresholds]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None, Thresholds()
    baseline = Baseline(**data["baseline"]) if data.get("baseline") else None
    known = Thresholds.__dataclass_fields__
    thresholds = Thresholds(**{k: v for k, v in data.get("thresholds", {}).items() if k in known})
    return baseline, thresholds


def save(baseline: Baseline | None, thresholds: Thresholds) -> None:
    data = {
        "baseline": asdict(baseline) if baseline else None,
        "thresholds": asdict(thresholds),
    }
    _file().write_text(json.dumps(data, indent=2), encoding="utf-8")

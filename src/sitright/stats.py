"""Per-day summary: how much of the time you sat upright, breaks taken, reminders shown.

One small JSON file per day in <config dir>/stats/YYYY-MM-DD.json. The tracker
itself is plain Python; reading and writing the files is passed in.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

MAX_STEP_SECONDS = 1.0  # gaps longer than this (paused, camera gone) aren't counted


@dataclass
class DayStats:
    day: str  # ISO date, e.g. "2026-03-02"
    seen_seconds: float = 0.0  # time you were at the desk and in view
    upright_seconds: float = 0.0  # ...of which posture and distance were fine
    breaks: int = 0
    reminders: dict[str, int] = field(default_factory=dict)  # Alert name -> count

    @property
    def upright_percent(self) -> int | None:
        if self.seen_seconds < 60:  # too little to say anything
            return None
        return round(100 * self.upright_seconds / self.seen_seconds)

    def summary(self) -> str:
        pct = self.upright_percent
        if pct is None:
            return "Today: not enough data yet"
        breaks = f"{self.breaks} break" + ("" if self.breaks == 1 else "s")
        return f"Today: {pct}% upright, {breaks}"

    @classmethod
    def from_dict(cls, data: dict, day: str) -> "DayStats":
        """Lenient: a damaged file just starts the day fresh for the bad fields."""
        s = cls(day)
        for key in ("seen_seconds", "upright_seconds"):
            if isinstance(data.get(key), (int, float)) and not isinstance(data.get(key), bool):
                setattr(s, key, float(data[key]))
        if isinstance(data.get("breaks"), int) and not isinstance(data.get("breaks"), bool):
            s.breaks = data["breaks"]
        if isinstance(data.get("reminders"), dict):
            s.reminders = {
                k: v for k, v in data["reminders"].items() if isinstance(v, int) and v >= 0
            }
        return s


class StatsTracker:
    def __init__(
        self,
        away_reset_seconds: float,
        load: Callable[[str], DayStats | None] = lambda day: None,
        save: Callable[[DayStats], None] = lambda stats: None,
        save_every: float = 60.0,
    ) -> None:
        self.away_reset_seconds = away_reset_seconds
        self._load = load
        self._save = save
        self._save_every = save_every
        self.today: DayStats | None = None
        self._last: float | None = None
        self._away_since: float | None = None
        self._next_save = 0.0

    def _roll(self, day: date) -> None:
        iso = day.isoformat()
        if self.today is not None and self.today.day == iso:
            return
        if self.today is not None:
            self._save(self.today)
        self.today = self._load(iso) or DayStats(iso)

    def observe(self, face: bool, upright: bool, now: float, day: date) -> None:
        """Feed one frame's result. `now` is a monotonic clock, `day` the local date."""
        self._roll(day)
        dt = 0.0 if self._last is None else now - self._last
        self._last = now
        if 0 < dt <= MAX_STEP_SECONDS and face:
            self.today.seen_seconds += dt
            if upright:
                self.today.upright_seconds += dt
        if not face:
            if self._away_since is None:
                self._away_since = now
        else:
            if self._away_since is not None and now - self._away_since >= self.away_reset_seconds:
                self.today.breaks += 1
            self._away_since = None
        if now >= self._next_save:
            self._next_save = now + self._save_every
            self._save(self.today)

    def reminder(self, name: str, day: date) -> None:
        self._roll(day)
        self.today.reminders[name] = self.today.reminders.get(name, 0) + 1

    def flush(self) -> None:
        if self.today is not None:
            self._save(self.today)

    def summary(self) -> str:
        return self.today.summary() if self.today else "Today: not enough data yet"


def stats_dir() -> Path:
    from .config import config_dir

    path = config_dir() / "stats"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_day(day: str) -> DayStats | None:
    try:
        data = json.loads((stats_dir() / f"{day}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return DayStats.from_dict(data, day) if isinstance(data, dict) else None


def save_day(stats: DayStats) -> None:
    data = asdict(stats)
    data["upright_percent"] = stats.upright_percent  # handy when reading the file yourself
    try:
        (stats_dir() / f"{stats.day}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass  # stats are nice to have; never stop the app over them

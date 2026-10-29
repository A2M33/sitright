"""Turns per-frame face measurements into reminders.

Everything here is plain Python with no camera or UI, so it can be tested
with fake samples (see tests/test_monitor.py).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from statistics import median


def eye_aspect_ratio(points: list[tuple[float, float]]) -> float:
    """Height of the eye divided by its width. Drops sharply when the eye closes."""
    p1, p2, p3, p4, p5, p6 = points
    width = math.dist(p1, p4)
    if width == 0:
        return 0.0
    return (math.dist(p2, p6) + math.dist(p3, p5)) / (2 * width)


@dataclass(frozen=True)
class Sample:
    """What the detector measured in one frame. Coordinates are 0..1 of the frame."""

    ear: float  # eye aspect ratio, average of both eyes (small = closed)
    nose_y: float  # vertical position of the nose tip
    face_width: float  # cheek-to-cheek distance, grows as you lean in


@dataclass
class Baseline:
    """Your 'sitting well' pose, captured during calibration."""

    nose_y: float
    face_width: float
    open_ear: float

    @classmethod
    def from_samples(cls, samples: list[Sample]) -> "Baseline":
        if not samples:
            raise ValueError("no face seen during calibration")
        return cls(
            nose_y=median(s.nose_y for s in samples),
            face_width=median(s.face_width for s in samples),
            open_ear=median(s.ear for s in samples),
        )


@dataclass
class Thresholds:
    slouch_drop: float = 0.07  # nose this much lower than baseline (fraction of frame height)
    too_close_ratio: float = 1.25  # face this much wider than baseline
    hold_seconds: float = 30.0  # bad posture must last this long before we say anything
    min_blinks_per_minute: float = 8.0  # healthy is ~15-20; staring at screens drops it a lot
    blink_window_seconds: float = 60.0
    break_after_minutes: float = 50.0
    away_reset_seconds: float = 120.0  # leaving this long counts as a break
    cooldown_seconds: float = 300.0  # same reminder at most every 5 minutes
    face_lost_grace_seconds: float = 1.5  # shorter face dropouts don't reset anything


class Alert(Enum):
    SLOUCH = "Sit up straight. You've been slouching for a bit."
    TOO_CLOSE = "You're very close to the screen. Lean back a little."
    LOW_BLINK = "You're barely blinking. Look away and blink a few times."
    TAKE_BREAK = "You've been sitting for a long time. Stand up and stretch."


@dataclass
class Status:
    face: bool = False
    slouching: bool = False
    too_close: bool = False
    blinks_per_minute: float | None = None
    sitting_minutes: float = 0.0

    @property
    def ok(self) -> bool:
        return self.face and not (self.slouching or self.too_close)


@dataclass
class Monitor:
    baseline: Baseline
    thresholds: Thresholds = field(default_factory=Thresholds)

    def __post_init__(self) -> None:
        self.status = Status()
        self._bad_since: dict[Alert, float] = {}
        self._last_alert: dict[Alert, float] = {}
        self._blinks: deque[float] = deque()
        self._eyes_closed = False
        self._face_since: float | None = None  # start of the current blink-measuring stretch
        self._seated_since: float | None = None
        self._last_seen: float | None = None
        self._last_update: float | None = None

    def update(self, sample: Sample | None, now: float) -> list[Alert]:
        """Feed one frame (None = no face). Returns reminders to show right now."""
        th = self.thresholds
        last, self._last_update = self._last_update, now
        if last is not None and now - last >= th.away_reset_seconds:
            # No frames for a long while (camera gone, paused): we can't tell if you
            # stayed, so count it as a break rather than nagging the moment we're back.
            self._forget()
        if sample is None:
            if self._last_seen is not None and now - self._last_seen < th.face_lost_grace_seconds:
                # The model misses a frame now and then (a hand on the chin, a quick
                # turn). Keep the timers; only a longer absence counts as gone.
                return []
            self.status.face = False
            self._bad_since.clear()
            self._face_since = None
            self._blinks.clear()
            if self._last_seen is not None and now - self._last_seen >= th.away_reset_seconds:
                self._seated_since = None
                self.status.sitting_minutes = 0.0
            return []

        self.status.face = True
        self._last_seen = now
        if self._seated_since is None:
            self._seated_since = now
        if self._face_since is None:
            self._face_since = now

        self._track_blink(sample, now)

        self.status.slouching = sample.nose_y - self.baseline.nose_y > th.slouch_drop
        self.status.too_close = sample.face_width > self.baseline.face_width * th.too_close_ratio
        self.status.sitting_minutes = (now - self._seated_since) / 60

        alerts = []
        for alert, bad in (
            (Alert.SLOUCH, self.status.slouching),
            (Alert.TOO_CLOSE, self.status.too_close),
        ):
            if not bad:
                self._bad_since.pop(alert, None)
                continue
            since = self._bad_since.setdefault(alert, now)
            if now - since >= th.hold_seconds:
                alerts.append(alert)

        bpm = self.status.blinks_per_minute
        if bpm is not None and bpm < th.min_blinks_per_minute:
            alerts.append(Alert.LOW_BLINK)
        if self.status.sitting_minutes >= th.break_after_minutes:
            alerts.append(Alert.TAKE_BREAK)

        return [a for a in alerts if self._cooled_down(a, now)]

    def _forget(self) -> None:
        self._bad_since.clear()
        self._face_since = None
        self._blinks.clear()
        self._seated_since = None
        self._last_seen = None
        self.status = Status()

    def _track_blink(self, sample: Sample, now: float) -> None:
        # Relative to your own open-eye value, so glasses and eye shape don't matter.
        closed = sample.ear < self.baseline.open_ear * 0.7
        if self._eyes_closed and not closed:
            self._blinks.append(now)
        self._eyes_closed = closed

        window = self.thresholds.blink_window_seconds
        while self._blinks and now - self._blinks[0] > window:
            self._blinks.popleft()
        # Only judge the blink rate once we've watched a full window.
        if self._face_since is not None and now - self._face_since >= window:
            self.status.blinks_per_minute = len(self._blinks) * 60 / window
        else:
            self.status.blinks_per_minute = None

    def _cooled_down(self, alert: Alert, now: float) -> bool:
        last = self._last_alert.get(alert)
        if last is not None and now - last < self.thresholds.cooldown_seconds:
            return False
        self._last_alert[alert] = now
        if alert is Alert.TAKE_BREAK:
            # Snooze: ask again after another full stretch, not every 5 minutes.
            self._last_alert[alert] = now + self.thresholds.break_after_minutes * 60 / 2
        return True

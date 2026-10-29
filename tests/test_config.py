import json

import pytest

from sitright import config
from sitright.monitor import Baseline


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "sitright"


def test_round_trip(cfg_dir):
    s = config.Settings(baseline=Baseline(0.5, 0.3, 0.3, pitch=1.0), camera=2)
    s.reminders.blink = True
    s.thresholds.hold_seconds = 45
    config.save(s)
    loaded = config.load()
    assert loaded == s


def test_missing_file_gives_defaults(cfg_dir):
    assert config.load() == config.Settings()


def test_old_file_without_camera_or_reminders(cfg_dir):
    cfg_dir.mkdir()
    old = {"baseline": {"nose_y": 0.5, "face_width": 0.3, "open_ear": 0.3}, "thresholds": {}}
    (cfg_dir / "config.json").write_text(json.dumps(old), encoding="utf-8")
    s = config.load()
    assert s.camera == 0
    assert s.baseline.pitch is None
    assert s.reminders.blink is False
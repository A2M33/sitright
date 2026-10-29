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

def write(cfg_dir, data):
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "config.json").write_text(json.dumps(data), encoding="utf-8")


def test_wrong_types_fall_back_per_field(cfg_dir):
    write(cfg_dir, {
        "baseline": {"nose_y": 0.5, "face_width": 0.3, "open_ear": 0.3, "colour": "blue"},
        "thresholds": {"hold_seconds": "thirty", "slouch_drop": 0.1, "cooldown_seconds": True},
        "reminders": {"blink": "yes", "slouch": False},
        "camera": "1",
        "sensitivity": "extreme",
        "auto_pause": 0,
        "theme": "dark",
    })
    s, problems = config.read()  # used to raise TypeError at startup
    assert s.baseline == Baseline(0.5, 0.3, 0.3)  # unknown key skipped, pose kept
    assert s.thresholds.hold_seconds == 30  # bad value -> default
    assert s.thresholds.slouch_drop == 0.1  # good value next to it survives
    assert s.thresholds.cooldown_seconds == 300
    assert s.reminders.blink is False and s.reminders.slouch is False
    assert (s.camera, s.sensitivity, s.auto_pause) == (0, "normal", True)
    text = " ".join(problems)
    for name in ("hold_seconds", "cooldown_seconds", "reminders.blink", "camera", "sensitivity",
                 "auto_pause", "theme", "colour"):
        assert name in text


def test_ints_are_fine_for_float_settings(cfg_dir):
    write(cfg_dir, {"thresholds": {"hold_seconds": 45}})
    s, problems = config.read()
    assert s.thresholds.hold_seconds == 45.0 and problems == []


def test_incomplete_or_odd_files(cfg_dir):
    write(cfg_dir, {"baseline": {"nose_y": 0.5}, "thresholds": [1, 2]})
    s, problems = config.read()
    assert s.baseline is None
    assert s.thresholds == config.Thresholds()
    assert any("Calibrate again" in p for p in problems)
    write(cfg_dir, [1, 2, 3])
    assert config.read()[0] == config.Settings()
    (cfg_dir / "config.json").write_text("{not json", encoding="utf-8")
    s, problems = config.read()
    assert s == config.Settings() and problems


def test_calibration_save_keeps_threshold_edits(cfg_dir):
    config.save(config.Settings())
    settings = config.load()  # the app starts with hold_seconds = 30
    # While it runs, the user edits config.json by hand.
    data = json.loads((cfg_dir / "config.json").read_text(encoding="utf-8"))
    data["thresholds"]["hold_seconds"] = 60
    data["my_note"] = "keep me"
    (cfg_dir / "config.json").write_text(json.dumps(data), encoding="utf-8")
    # Then calibrates (or flips a menu toggle), which saves.
    settings.baseline = Baseline(0.4, 0.3, 0.3)
    config.save(settings)
    saved = json.loads((cfg_dir / "config.json").read_text(encoding="utf-8"))
    assert saved["thresholds"]["hold_seconds"] == 60
    assert saved["my_note"] == "keep me"
    assert saved["baseline"]["nose_y"] == 0.4
    assert config.load().thresholds.hold_seconds == 60


def test_save_fills_in_missing_threshold_keys(cfg_dir):
    write(cfg_dir, {"thresholds": {"hold_seconds": 50}})
    config.save(config.load())
    saved = json.loads((cfg_dir / "config.json").read_text(encoding="utf-8"))
    assert saved["thresholds"]["hold_seconds"] == 50
    assert saved["thresholds"]["slouch_drop"] == 0.07
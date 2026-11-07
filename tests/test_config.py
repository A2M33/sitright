import json
import threading

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


def test_a_crash_while_saving_leaves_the_old_file_whole(cfg_dir, monkeypatch):
    config.save(config.Settings(camera=3))

    def boom(_src, _dst):
        raise OSError("power cut")

    with monkeypatch.context() as patch:
        patch.setattr(config.os, "replace", boom)
        with pytest.raises(OSError):
            config.save(config.Settings(camera=4))
    assert config.read() == (config.Settings(camera=3), [])


def test_saves_from_two_threads_never_mix(cfg_dir):
    errors = []

    def spam(camera):
        try:
            for _ in range(40):
                config.save(config.Settings(camera=camera))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=spam, args=(i,)) for i in (1, 2, 3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    settings, problems = config.read()
    assert settings.camera in (1, 2, 3) and problems == []


def test_saving_over_a_damaged_file_keeps_a_copy(cfg_dir):
    cfg_dir.mkdir()
    broken = '{"thresholds": {"hold_seconds": 45,}}'  # one trailing comma, hours of tuning
    (cfg_dir / "config.json").write_text(broken, encoding="utf-8")
    config.save(config.Settings(camera=1))
    assert (cfg_dir / "config.json.bad").read_text(encoding="utf-8") == broken
    assert config.read() == (config.Settings(camera=1), [])


def test_a_file_saved_with_a_byte_order_mark_is_read(cfg_dir):
    cfg_dir.mkdir()
    edited = '{"thresholds": {"hold_seconds": 45}}'
    (cfg_dir / "config.json").write_text(edited, encoding="utf-8-sig")  # Notepad, PowerShell
    settings, problems = config.read()
    assert problems == [] and settings.thresholds.hold_seconds == 45
    config.save(settings)  # used to treat the file as damaged and drop the edit
    assert config.load().thresholds.hold_seconds == 45
    assert not (cfg_dir / "config.json.bad").exists()


def test_a_save_that_cant_read_the_file_leaves_it_alone(cfg_dir, monkeypatch):
    config.save(config.Settings(camera=1))

    def locked():
        raise PermissionError("in use")

    with monkeypatch.context() as patch:
        patch.setattr(config, "_read_raw", locked)
        with pytest.raises(OSError):  # used to carry on and wipe the hand-edited thresholds
            config.save(config.Settings(camera=2))
    assert config.load().camera == 1


def test_an_absurdly_long_number_is_a_problem_not_a_crash(cfg_dir):
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text('{"camera": ' + "1" * 5000 + "}", encoding="utf-8")
    s, problems = config.read()
    assert s == config.Settings() and problems


def test_thresholds_that_would_crash_the_engine_fall_back(cfg_dir):
    write(cfg_dir, {"thresholds": {
        "blink_window_seconds": 0,  # used to divide by zero and kill the engine thread
        "away_reset_seconds": 0,
        "hold_seconds": -5,
        "slouch_drop": 0.1,
        "cooldown_seconds": 0,  # no cooldown is a legitimate choice
    }})
    s, problems = config.read()
    default = config.Thresholds()
    assert s.thresholds.blink_window_seconds == default.blink_window_seconds
    assert s.thresholds.away_reset_seconds == default.away_reset_seconds
    assert s.thresholds.hold_seconds == default.hold_seconds
    assert s.thresholds.slouch_drop == 0.1
    assert s.thresholds.cooldown_seconds == 0
    assert len(problems) == 3 and "blink_window_seconds" in problems[0]


def test_nan_and_infinity_are_not_numbers_we_can_use(cfg_dir):
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(
        '{"thresholds": {"hold_seconds": NaN, "slouch_drop": Infinity, "cooldown_seconds": 1e999,'
        ' "break_after_minutes": ' + "9" * 400 + "}}",
        encoding="utf-8",
    )
    s, problems = config.read()
    assert s.thresholds == config.Thresholds()
    assert len(problems) == 4


def test_a_pose_that_cant_be_real_asks_for_calibration(cfg_dir):
    write(cfg_dir, {"baseline": {"nose_y": 0.5, "face_width": 0, "open_ear": 0.3}})
    s, problems = config.read()
    assert s.baseline is None
    assert any("Calibrate again" in p for p in problems)

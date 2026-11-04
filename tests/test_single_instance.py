import sys
import uuid

import pytest

from sitright import __main__ as cli
from sitright import single_instance


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


def test_second_claim_is_refused_until_the_first_lets_go(cache):
    name = f"test-{uuid.uuid4().hex}"
    release = single_instance.acquire(name)
    assert release is not None
    assert single_instance.acquire(name) is None
    release()
    again = single_instance.acquire(name)
    assert again is not None
    again()


def test_a_second_tray_app_says_so_and_stops(monkeypatch, capsys):
    from sitright import app

    started = []
    monkeypatch.setattr(sys, "argv", ["sitright"])
    monkeypatch.setattr(app, "run_tray", started.append)
    monkeypatch.setattr(single_instance, "acquire", lambda: None)
    cli.main()
    assert started == []  # both would have opened the camera and rewritten config.json
    assert "already running" in capsys.readouterr().out


def test_the_tray_app_lets_go_when_it_ends(monkeypatch):
    from sitright import app

    released = []
    monkeypatch.setattr(sys, "argv", ["sitright"])
    monkeypatch.setattr(app, "run_tray", lambda camera: None)
    monkeypatch.setattr(single_instance, "acquire", lambda: lambda: released.append(True))
    cli.main()
    assert released == [True]


def test_preview_needs_no_claim(monkeypatch):
    from sitright import app

    ran = []
    monkeypatch.setattr(sys, "argv", ["sitright", "--preview"])
    monkeypatch.setattr(app, "run_preview", ran.append)
    monkeypatch.setattr(single_instance, "acquire", lambda: None)
    cli.main()
    assert ran == [None]

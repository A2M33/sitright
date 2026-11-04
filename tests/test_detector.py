"""The parts of the detector that need no camera: getting the face model onto disk."""

import io
from email.message import Message

import pytest

pytest.importorskip("cv2", exc_type=ImportError)
pytest.importorskip("mediapipe", exc_type=ImportError)

from sitright import detector  # noqa: E402
from sitright.engine import ModelError  # noqa: E402


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, length: int | None = None) -> None:
        super().__init__(body)
        self.headers = Message()
        if length is not None:
            self.headers["Content-Length"] = str(length)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(detector, "cache_dir", lambda: tmp_path)
    return tmp_path


def serve(monkeypatch, result):
    def urlopen(_url, timeout=None):
        assert timeout  # a hung server must not hang the engine thread for ever
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(detector.urllib.request, "urlopen", urlopen)


def test_model_is_downloaded_once(cache, monkeypatch):
    serve(monkeypatch, FakeResponse(b"model", 5))
    path = detector.model_path()
    assert path.read_bytes() == b"model"
    serve(monkeypatch, OSError("no network"))
    assert detector.model_path() == path


def test_no_network_gives_a_clear_error(cache, monkeypatch):
    serve(monkeypatch, OSError("getaddrinfo failed"))
    with pytest.raises(ModelError, match="internet connection"):
        detector.model_path()
    assert list(cache.iterdir()) == []


def test_a_cut_off_download_is_not_kept(cache, monkeypatch):
    serve(monkeypatch, FakeResponse(b"mod", 5))
    with pytest.raises(ModelError):
        detector.model_path()
    assert list(cache.iterdir()) == []


def test_camera_stays_off_when_the_model_is_missing(cache, monkeypatch):
    serve(monkeypatch, OSError("no network"))
    opened = []
    monkeypatch.setattr(detector.cv2, "VideoCapture", opened.append)
    with pytest.raises(ModelError):
        detector.Detector(0)
    assert opened == []  # used to open first and leave the camera held after the failure

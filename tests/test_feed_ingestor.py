"""Feed ingestor: single-JPEG snapshot URLs (motionEye) are read directly, streams fall through to OpenCV."""
import importlib.util
import pathlib
from contextlib import contextmanager

import pytest

from test_pipeline import jpeg

spec = importlib.util.spec_from_file_location("feed_ingestor", pathlib.Path(__file__).parents[1] / "services/feed-ingestor/main.py")
fi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fi)


class FakeResp:
    def __init__(self, status, ctype, body=b""):
        self.status_code, self.headers, self._b = status, {"content-type": ctype}, body

    def read(self):
        return self._b


def patch_stream(monkeypatch, resp):
    @contextmanager
    def fake(method, url, **kw):
        yield resp
    monkeypatch.setattr(fi.httpx, "stream", fake)


def test_snapshot_url_returns_jpeg(monkeypatch):
    patch_stream(monkeypatch, FakeResp(200, "image/jpeg", jpeg()))
    feed = {"tenant": "t", "camera": "c", "url": "http://192.168.1.4:8765/picture/1/current/"}
    assert fi.grab(feed) == jpeg()


def test_mjpeg_stream_is_not_read_as_snapshot(monkeypatch):
    patch_stream(monkeypatch, FakeResp(200, "multipart/x-mixed-replace; boundary=x"))
    assert fi.grab_snapshot("http://cam/") is None


def test_camera_down_raises_cleanly(monkeypatch):
    def boom(*a, **k):
        raise fi.httpx.ConnectError("no route")
    monkeypatch.setattr(fi.httpx, "stream", boom)
    with pytest.raises(RuntimeError):
        fi.grab({"tenant": "t", "camera": "c", "url": "http://192.168.1.4/x"})


def test_feed_spec_parsing():
    f = fi.parse_feeds("home_1_bangalore:home_seniors:kitchen=http://192.168.1.4:8765/picture/1/current/")
    assert f == [{"tenant": "home_1_bangalore", "vertical": "home_seniors", "camera": "kitchen",
                  "url": "http://192.168.1.4:8765/picture/1/current/"}]

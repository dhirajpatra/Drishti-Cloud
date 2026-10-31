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


def test_saved_cameras_override_env_and_disabled_ones_are_dropped(monkeypatch):
    from drishti import db, siteconfig
    monkeypatch.setenv("RTSP_FEEDS", "mall_1:mall_security:cam_1=http://old/,mall_1:mall_security:cam_2=http://two/")
    db.upsert_camera_source("mall_1", "cam_1", "http", "10.0.0.9", 8081, "/pic/", "", None, True)
    db.upsert_camera_source("mall_1", "cam_2", "http", "10.0.0.8", None, "/", "", None, False)
    feeds = fi.all_feeds()
    assert feeds[("mall_1", "cam_1")]["url"] == "http://10.0.0.9:8081/pic/"
    assert ("mall_1", "cam_2") not in feeds                       # switched off in the Admin page
    db.delete_camera_source("mall_1", "cam_1"); db.delete_camera_source("mall_1", "cam_2")


def test_motion_trigger_grabs_the_named_feed_with_pir_true(monkeypatch):
    import json
    from drishti import db
    monkeypatch.setenv("RTSP_FEEDS", "home_1_bangalore:home_seniors:kitchen=http://192.168.1.4:8081/")
    db.upsert_camera_source("school_1", "gate", "rtsp", "10.1.1.1", None, "/s", "", None, True)
    pushed = []
    monkeypatch.setattr(fi, "push", lambda feed, sensors=None, burst=1: pushed.append((feed["camera"], sensors)))
    assert fi.handle_trigger(json.dumps({"tenant": "home_1_bangalore", "camera": "kitchen"})) is True
    assert fi.handle_trigger(json.dumps({"tenant": "school_1", "camera": "gate"})) is True      # Admin-saved camera
    assert fi.handle_trigger(json.dumps({"tenant": "home_1_bangalore", "camera": "attic"})) is False
    assert pushed == [("kitchen", {"source": "motioneye_hook", "pir": True}), ("gate", {"source": "motioneye_hook", "pir": True})]
    db.delete_camera_source("school_1", "gate")


def test_each_camera_is_sampled_at_its_own_interval():
    fi._due.clear()
    feeds = {("t", "a"): {"interval": 30}, ("t", "b"): {"interval": 300}}
    assert len(fi.due_feeds(feeds, 1000.0)) == 2
    assert [f["interval"] for f in fi.due_feeds(feeds, 1031.0)] == [30]
    assert fi.due_feeds(feeds, 1032.0) == []


def test_burst_posts_the_newest_frame_as_main_and_earlier_ones_as_burst(monkeypatch):
    seq = iter([b"f0", b"f1", b"f2", b"f3"])
    monkeypatch.setattr(fi, "grab", lambda feed: next(seq))
    monkeypatch.setattr(fi.time, "sleep", lambda s: None)
    sent = {}

    class R:
        status_code = 200

        def json(self):
            return {"static": False, "change": None}

    monkeypatch.setattr(fi.httpx, "post", lambda url, **kw: sent.update(kw) or R())
    fi.push({"tenant": "t", "camera": "c", "url": "http://x/"}, {"pir": True}, burst=4)
    names = [(f[0], f[1][1]) for f in sent["files"]]
    assert names == [("file", b"f3"), ("burst", b"f0"), ("burst", b"f1"), ("burst", b"f2")]


def test_missed_frames_in_a_burst_do_not_lose_the_rest(monkeypatch):
    seq = iter([b"a", RuntimeError("blip"), b"c"])

    def grab(feed):
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(fi, "grab", grab)
    monkeypatch.setattr(fi.time, "sleep", lambda s: None)
    assert fi.grab_burst({"tenant": "t", "camera": "c", "url": "u"}, 3, 0.1) == [b"a", b"c"]

"""OCR 'leans in': crops of monitors / signs from the hi-res frame, else the whole hi-res frame, else the normal one."""
import importlib.util
import io
import pathlib

from PIL import Image

from drishti import bus

spec = importlib.util.spec_from_file_location("ocr_reader", pathlib.Path(__file__).parents[1] / "services/ocr-reader/main.py")
ocr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ocr)


def big(w=1280, h=960):
    im = Image.new("RGB", (w, h), (30, 30, 30))
    b = io.BytesIO()
    im.save(b, "JPEG")
    return b.getvalue()


def test_large_frame_keeps_a_hires_copy_only_when_analysed(client, redis_c):
    redis_c.flushdb()
    r = client.post("/ingest/icu_ward_1", files={"file": ("f.jpg", big(), "image/jpeg")}, data={"camera_id": "bed5"}).json()
    assert Image.open(io.BytesIO(bus.get_hires(r["frame_id"]))).size == (1280, 960)
    assert Image.open(io.BytesIO(bus.get_frame(r["frame_id"]))).size[0] <= 640           # the models still get the small one
    s = client.post("/ingest/icu_ward_1", files={"file": ("f.jpg", big(), "image/jpeg")}, data={"camera_id": "bed5"}).json()
    assert s["static"] is True and bus.get_hires(s["frame_id"]) is None                   # idle frame: nothing extra stored
    small = client.post("/ingest/icu_ward_1", files={"file": ("f.jpg", big(400, 300), "image/jpeg")}, data={"camera_id": "tiny"}).json()
    assert bus.get_hires(small["frame_id"]) is None                                       # nothing sharper to offer


def test_zoom_crops_the_monitor(redis_c):
    bus.put_hires("fz", big())
    p = {"frame_id": "fz", "objects": [{"label": "monitor", "bbox": [0.5, 0.1, 0.8, 0.3]}, {"label": "bed", "bbox": [0, 0.5, 1, 1]}]}
    crop = Image.open(io.BytesIO(ocr.zoom_image(p)))
    assert crop.width <= 900 and crop.width > 300 and crop.height < 500                    # only the monitor, enlarged
    p2 = {"frame_id": "fz", "objects": [{"label": "bed", "bbox": [0, 0.5, 1, 1]}]}
    assert Image.open(io.BytesIO(ocr.zoom_image(p2))).size == (1280, 960)                  # no text object: whole hi-res frame
    assert ocr.zoom_image({"frame_id": "missing", "objects": []}) is None                  # nothing stored: caller uses the normal frame
    many = {"frame_id": "fz", "objects": [{"label": "sign", "bbox": [0.1 * i, 0.1, 0.1 * i + 0.08, 0.2]} for i in range(6)]}
    assert Image.open(io.BytesIO(ocr.zoom_image(many))).height > 0                          # at most 3 crops are stacked

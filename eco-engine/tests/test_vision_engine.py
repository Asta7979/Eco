"""
Tests for photo assistance (computer vision). The neural detector is not loaded here,
so exterior photos exercise the OpenCV fallback; thermal segmentation is deterministic.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.engine import vision_engine as ve

ASSETS = Path(__file__).resolve().parent.parent / "frontend" / "web" / "assets"


@pytest.fixture(autouse=True)
def no_neural_model(monkeypatch):
    monkeypatch.setenv("ECO_VISION_WARMUP", "0")
    monkeypatch.setattr(ve.yolo_world, "detect", lambda img: None)


def _jpeg(img):
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _synthetic_thermal(hot_roof=True, hot_windows=True):
    t = np.full((480, 640), 0.15, np.float32)
    cv2.rectangle(t, (120, 110), (520, 420), 0.35, -1)
    if hot_roof:
        cv2.line(t, (100, 110), (540, 110), 0.85, 16)  # eaves at ~23% of the frame height
    if hot_windows:
        for x in (170, 400):
            cv2.rectangle(t, (x, 260), (x + 70, 330), 0.85, -1)
    t = cv2.GaussianBlur(t, (0, 0), 3)
    return cv2.applyColorMap((t * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)


def test_thermal_hotspots_map_to_measures():
    res = ve.analyze_image(_jpeg(_synthetic_thermal()), "thermal")
    codes = {s["measure_code"] for s in res["suggestions"] if s["kind"] == "add_measure"}
    assert {"loft_insulation", "window_upgrade", "draught_proofing"} <= codes
    assert res["thermal"]["heat_loss_index"] > 0
    assert all(d["polygon"] for d in res["detections"] if d["source"] == "thermal-segmentation")
    assert res["image_stored"] is False


def test_uniform_thermal_image_has_no_hotspot_measures():
    flat = cv2.applyColorMap(np.full((300, 400), 90, np.uint8), cv2.COLORMAP_INFERNO)
    res = ve.analyze_image(_jpeg(flat), "thermal")
    assert not [s for s in res["suggestions"] if s["kind"] == "add_measure"]


def test_auto_mode_recognises_thermal_palette():
    assert ve.analyze_image(_jpeg(_synthetic_thermal()), "auto")["mode"] == "thermal"
    house = (ASSETS / "eco_house_3d.jpg").read_bytes()
    assert ve.analyze_image(house, "auto")["mode"] == "exterior"


def test_exterior_falls_back_to_opencv_and_boxes_are_normalised():
    res = ve.analyze_image((ASSETS / "eco_house_3d.jpg").read_bytes(), "exterior")
    assert "opencv-geometry" in res["engines_used"]
    for d in res["detections"]:
        x1, y1, x2, y2 = d["box"]
        assert 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1


@pytest.mark.parametrize("failure", ["load", "predict"])
def test_aerial_uses_yolov8_when_yolo26_fails(monkeypatch, failure):
    monkeypatch.setattr(ve, "solar_yolo", ve.solar_yolo26)
    monkeypatch.setattr(ve.solar_yolo26, "error", "load failed" if failure == "load" else None)

    def broken(_img):
        raise RuntimeError("YOLO26 inference failed")

    monkeypatch.setattr(ve.solar_yolo26, "detect", broken)
    monkeypatch.setattr(ve.solar_yolov8, "error", None)
    monkeypatch.setattr(ve.solar_yolov8, "load", lambda: object())
    monkeypatch.setattr(ve.solar_yolov8, "detect", lambda _img: [
        ve.Detection("Solar panel", 0.9, (0.1, 0.1, 0.3, 0.3), "solar-yolov8")
    ])

    res = ve.analyze_image(_jpeg(np.zeros((100, 100, 3), np.uint8)), "aerial")
    assert res["engines_used"] == ["solar-yolov8"]
    assert res["counts"]["Solar panel"] == 1


@pytest.mark.parametrize("data", [b"", b"not an image"])
def test_bad_uploads_are_rejected(data):
    with pytest.raises(ValueError):
        ve.analyze_image(data, "auto")

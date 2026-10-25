"""
Photo assistance (FR6): computer vision that suggests retrofit evidence from home photos.

Two modes:

* exterior  Open-vocabulary object detection with YOLO-World (windows, doors, solar
            panels, chimneys, heat-pump / AC units, ...). When the neural model is not
            available it falls back to a classical OpenCV detector for rectangular
            window-like openings, so the feature always works.
* aerial    Top-down / satellite roof imagery: solar panels with the YOLOv8 model trained
            on satellite data (ArielDrabkin/Solar-Panel-Detector).
* Trained facade detectors (elaounijassim/Detection-of-low-facade-openings-using-YOLOv8-and-SAM):
            YOLOv8s for windows and for doors / basement openings / garages. When present they
            replace YOLO-World for openings; YOLO-World keeps the other prompts.
* SAM (facebookresearch/segment-anything, ViT-B) turns detection boxes into pixel masks via
            segment_image(); it is a separate, slower call so boxes show immediately.
* thermal   Heat-loss segmentation of a thermal-camera image (ironbow / rainbow /
            greyscale palettes). Pixels are mapped to a relative heat index, the hot
            tail is segmented with Otsu thresholding + morphology, and each connected
            region becomes a hotspot with its outline, area and position on the facade.

Rules (Responsible use, section 10 of the M1 approach):
* Images are processed in memory and never written to disk or the database.
* The output is a list of *suggestions*. The planner changes only after the user
  confirms them in the UI.
* Detections never set numbers in the engines. They only propose measures to add
  or flag ones that already look installed.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

MODEL_PATH = Path(
    os.getenv("ECO_VISION_MODEL", Path(__file__).resolve().parent.parent.parent / "models" / "yolov8s-worldv2.pt")
)
MAX_SIDE_PX = 1280
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_LIVE_BYTES = 2 * 1024 * 1024  # one compressed camera frame

# Prompt -> display label. YOLO-World matches these text prompts against the image.
EXTERIOR_PROMPTS: Dict[str, str] = {
    "window": "Window",
    "door": "Door",
    "solar panel": "Solar panel",
    "chimney": "Chimney",
    "heat pump outdoor unit": "Outdoor heat-pump / AC unit",
    "air conditioner": "Outdoor heat-pump / AC unit",
    "satellite dish": "Satellite dish",
    "garage door": "Garage door",
    "roof": "Roof",
}
DETECTION_CONF = float(os.getenv("ECO_VISION_CONF", "0.03"))
DETECTION_IMGSZ = 960
# Open-vocabulary scores are low for thin classes such as windows, so each prompt
# gets its own floor; anything below it is dropped.
PROMPT_MIN_CONF: Dict[str, float] = {
    "window": 0.035, "door": 0.08, "roof": 0.08,
    "heat pump outdoor unit": 0.3, "air conditioner": 0.3,
}
DEFAULT_MIN_CONF = 0.1
MIN_THERMAL_CONTRAST = 0.08  # heat-index spread below which a thermal frame counts as uniform


# ======================================================================
# Result types
# ======================================================================

@dataclass
class Detection:
    label: str
    confidence: float
    box: Tuple[float, float, float, float]  # x1, y1, x2, y2 normalised 0-1
    source: str  # "yolo-world" | "opencv-geometry" | "thermal-segmentation"
    polygon: Optional[List[Tuple[float, float]]] = None
    extra: Dict = field(default_factory=dict)


@dataclass
class Suggestion:
    kind: str  # "add_measure" | "already_installed" | "observation"
    title: str
    reason: str
    confidence: float
    measure_code: Optional[str] = None
    evidence: List[int] = field(default_factory=list)


# ======================================================================
# Image helpers
# ======================================================================

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_IMAGE_PIXELS = 40_000_000  # 40 MP: well above any phone photo, far below a decompression bomb


def _check_image_header(data: bytes) -> None:
    """
    Reads only the image header (no pixel decoding) and rejects anything that is not a
    JPEG / PNG / WebP or that declares more than 40 MP. A small compressed file can declare
    enormous dimensions; decoding it would exhaust memory (a decompression bomb).
    """
    import io
    import warnings

    from PIL import Image

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                fmt, (w, h) = im.format, im.size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("Image dimensions are too large.")
    except Exception:
        raise ValueError("Couldn't read that file as an image. Use JPG, PNG or WebP.")
    if fmt not in ALLOWED_IMAGE_FORMATS:
        raise ValueError("Use a JPG, PNG or WebP image.")
    if w * h > MAX_IMAGE_PIXELS:
        raise ValueError("Image dimensions are too large (over 40 megapixels).")


def decode_image(data: bytes) -> np.ndarray:
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Image is larger than 12 MB.")
    _check_image_header(data)
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Couldn't read that file as an image. Use JPG, PNG or WebP.")
    h, w = img.shape[:2]
    scale = MAX_SIDE_PX / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img


def image_quality(img: np.ndarray) -> Dict:
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(grey, cv2.CV_64F).var())
    brightness = float(grey.mean())
    warnings = []
    if sharpness < 60:
        warnings.append("The photo looks blurry, so detections may be missed.")
    if brightness < 45:
        warnings.append("The photo is very dark.")
    elif brightness > 225:
        warnings.append("The photo is overexposed.")
    return {
        "width": int(img.shape[1]),
        "height": int(img.shape[0]),
        "sharpness": round(sharpness, 1),
        "brightness": round(brightness, 1),
        "warnings": warnings,
    }


def looks_thermal(img: np.ndarray) -> bool:
    """Thermal palettes are dominated by saturated purple/red/yellow with little green."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sat = hsv[..., 1].astype(np.float32)
    hue = hsv[..., 0].astype(np.float32)  # 0-179
    saturated = sat > 90
    if saturated.mean() < 0.45:
        return False
    greens = ((hue > 35) & (hue < 85) & saturated).mean()
    warm_or_purple = (((hue < 30) | (hue > 125)) & saturated).mean()
    return warm_or_purple > 0.5 and greens < 0.1


def _norm_box(x1, y1, x2, y2, w, h) -> Tuple[float, float, float, float]:
    return (
        round(max(0.0, x1 / w), 4),
        round(max(0.0, y1 / h), 4),
        round(min(1.0, x2 / w), 4),
        round(min(1.0, y2 / h), 4),
    )


def _iou(a, b) -> float:
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _overlap_fraction(inner, outer) -> float:
    """Share of `inner` box covered by `outer` box."""
    ix1, iy1, ix2, iy2 = max(inner[0], outer[0]), max(inner[1], outer[1]), min(inner[2], outer[2]), min(inner[3], outer[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return inter / area if area > 0 else 0.0


# ======================================================================
# Neural detector: YOLO-World (lazy, thread-safe, optional)
# ======================================================================

class _YoloWorld:
    def __init__(self):
        self._model = None
        self._lock = threading.Lock()
        self._predict_lock = threading.Lock()
        self._error: Optional[str] = None
        self._loading = False

    def status(self) -> Dict:
        return {
            "model_file_present": MODEL_PATH.exists(),
            "loaded": self._model is not None,
            "loading": self._loading,
            "error": self._error,
        }

    def load(self):
        if self._model is not None or self._error:
            return self._model
        from backend.engine.safe_models import MODEL_LOAD_LOCK

        with MODEL_LOAD_LOCK:
            if self._model is not None or self._error:
                return self._model
            if not MODEL_PATH.exists():
                self._error = f"Model weights not found at {MODEL_PATH.name}."
                return None
            self._loading = True
            try:
                from ultralytics import YOLOWorld

                model = YOLOWorld(str(MODEL_PATH))
                model.set_classes(list(EXTERIOR_PROMPTS.keys()))
                self._model = model
                logger.info("YOLO-World loaded from %s", MODEL_PATH)
            except Exception as exc:  # missing CLIP, torch issues, corrupt weights
                self._error = f"{type(exc).__name__}: {exc}"[:300]
                logger.warning("YOLO-World unavailable, using OpenCV fallback: %s", self._error)
            finally:
                self._loading = False
        return self._model

    def detect(self, img: np.ndarray) -> Optional[List[Detection]]:
        # Never block a request on the first load (it may download CLIP weights):
        # start it in the background and let this request use the OpenCV fallback.
        model = self._model
        if model is None:
            if not self._loading and not self._error:
                warm_up_async()
            return None
        h, w = img.shape[:2]
        from backend.engine.safe_models import use_all_cores

        with self._predict_lock:  # ultralytics predictors are not re-entrant
            use_all_cores()
            results = model.predict(img, conf=DETECTION_CONF, iou=0.5, imgsz=DETECTION_IMGSZ, verbose=False)
        names = list(EXTERIOR_PROMPTS.keys())
        out: List[Detection] = []
        for r in results:
            for box, conf, cls in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist()):
                prompt = names[int(cls)] if int(cls) < len(names) else str(cls)
                if conf < PROMPT_MIN_CONF.get(prompt, DEFAULT_MIN_CONF):
                    continue
                out.append(
                    Detection(
                        label=EXTERIOR_PROMPTS.get(prompt, prompt),
                        confidence=round(float(conf), 3),
                        box=_norm_box(*box, w, h),
                        source="yolo-world",
                        extra={"prompt": prompt},
                    )
                )
        return out


yolo_world = _YoloWorld()


MODELS_DIR = MODEL_PATH.parent


class _TrainedYolo:
    """A fine-tuned YOLO checkpoint, loaded through safe_models (weights-only unpickler)."""

    def __init__(self, filename: str, labels: Dict[str, str], min_conf: float, source: str):
        self.path = MODELS_DIR / filename
        self.labels = labels
        self.min_conf = min_conf
        self.source = source
        self._model = None
        self._lock = threading.Lock()
        self._predict_lock = threading.Lock()
        self._loading = False
        self.error: Optional[str] = None

    def status(self) -> Dict:
        return {"file": self.path.name, "file_present": self.path.exists(), "loaded": self._model is not None,
                "loading": self._loading, "error": self.error}

    def load(self):
        if self._model is not None or self.error:
            return self._model
        from backend.engine.safe_models import MODEL_LOAD_LOCK

        with MODEL_LOAD_LOCK:
            if self._model is not None or self.error:
                return self._model
            if not self.path.exists():
                self.error = f"{self.path.name} not found."
                return None
            self._loading = True
            try:
                from backend.engine.safe_models import load_yolo

                self._model = load_yolo(self.path)
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"[:300]
                logger.warning("%s unavailable: %s", self.path.name, self.error)
            finally:
                self._loading = False
        return self._model

    def detect(self, img: np.ndarray, imgsz: int = 640) -> Optional[List[Detection]]:
        model = self._model
        if model is None:
            return None
        h, w = img.shape[:2]
        from backend.engine.safe_models import use_all_cores

        with self._predict_lock:
            use_all_cores()
            r = model.predict(img, conf=self.min_conf, iou=0.5, imgsz=imgsz, verbose=False)[0]
        out: List[Detection] = []
        for box, conf, cls in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist()):
            raw = r.names[int(cls)]
            out.append(Detection(label=self.labels.get(raw, raw), confidence=round(float(conf), 3),
                                 box=_norm_box(*box, w, h), source=self.source, extra={"class": raw}))
        return out


facade_windows = _TrainedYolo("facade_windows.pt", {"window": "Window"}, 0.25, "facade-yolov8")
facade_openings = _TrainedYolo("facade_openings.pt", {"door": "Door", "basement": "Basement opening", "garage": "Garage door"}, 0.25, "facade-yolov8")
# Rooftop solar: prefer the fine-tuned YOLO26s, retaining YOLOv8 if it fails to load or predict.
solar_yolo26 = _TrainedYolo("yolo26_solar.pt", {"item": "Solar panel", "Solar-Panel": "Solar panel"}, 0.25, "solar-yolo26")
solar_yolov8 = _TrainedYolo("solar_panel_yolov8.pt", {"solar-panel": "Solar panel"}, 0.25, "solar-yolov8")
solar_yolo = solar_yolo26 if solar_yolo26.path.exists() else solar_yolov8
TRAINED = {"facade_windows": facade_windows, "facade_openings": facade_openings, "solar_panels": solar_yolo}

SEGMENTABLE = {"Window", "Door", "Garage door", "Basement opening", "Solar panel", "Outdoor heat-pump / AC unit", "Chimney"}
MAX_SEGMENT_BOXES = 24


_warmup_guard = threading.Lock()
_warmup_started = False


def warm_up_async() -> None:
    """Loads the neural model in the background so the first upload is fast."""
    if os.getenv("ECO_VISION_WARMUP", "1") == "0":
        return
    global _warmup_started
    with _warmup_guard:
        if _warmup_started:
            return
        _warmup_started = True

    def _load_all():
        # One thread, one model at a time: fastest-to-useful first.
        for det in TRAINED.values():
            det.load()
        if solar_yolo is solar_yolo26 and solar_yolo26.error:
            solar_yolov8.load()
        yolo_world.load()
        from backend.engine.sam_segmenter import sam

        sam.load()

    from backend.engine.safe_models import INFERENCE_EXECUTOR

    INFERENCE_EXECUTOR.submit(_load_all)


def vision_status() -> Dict:
    from backend.engine.sam_segmenter import sam

    return {
        "modes": ["exterior", "thermal", "aerial"],
        "neural_detector": "YOLO-World v2 (open vocabulary)",
        "prompts": list(EXTERIOR_PROMPTS.values()),
        **yolo_world.status(),
        "trained_detectors": {k: v.status() for k, v in TRAINED.items()},
        "solar_fallback": solar_yolov8.status() if solar_yolo is solar_yolo26 else None,
        "segmenter": {"name": "Segment Anything (ViT-B)", **sam.status()},
        "fallback": "OpenCV geometric window detector",
        "images_stored": False,
    }


# ======================================================================
# Classical fallback: rectangular openings (windows) with OpenCV
# ======================================================================

def detect_openings_geometric(img: np.ndarray, max_items: int = 24) -> List[Detection]:
    h, w = img.shape[:2]
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    grey = cv2.bilateralFilter(grey, 7, 50, 50)
    edges = cv2.Canny(grey, 40, 120)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    candidates: List[Detection] = []
    img_area = float(h * w)
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        area = bw * bh
        if not (0.002 * img_area <= area <= 0.08 * img_area):
            continue
        aspect = bw / float(bh)
        if not (0.35 <= aspect <= 2.6):
            continue
        approx = cv2.approxPolyDP(c, 0.04 * cv2.arcLength(c, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        rectangularity = cv2.contourArea(approx) / float(area)
        if rectangularity < 0.75:
            continue
        # Glass usually reads darker or more uniform than the surrounding wall.
        inner = grey[y + bh // 6 : y + bh - bh // 6, x + bw // 6 : x + bw - bw // 6]
        ring = grey[max(0, y - 6) : y + bh + 6, max(0, x - 6) : x + bw + 6]
        if inner.size == 0 or ring.size == 0:
            continue
        contrast = abs(float(ring.mean()) - float(inner.mean())) / 255.0
        conf = min(0.6, 0.25 + 0.3 * rectangularity * min(1.0, contrast * 4))
        candidates.append(
            Detection(
                label="Window",
                confidence=round(conf, 3),
                box=_norm_box(x, y, x + bw, y + bh, w, h),
                source="opencv-geometry",
            )
        )

    candidates.sort(key=lambda d: d.confidence, reverse=True)
    kept: List[Detection] = []
    for d in candidates:
        if all(_iou(d.box, k.box) < 0.3 for k in kept):
            kept.append(d)
        if len(kept) >= max_items:
            break
    return kept


def detect_solar_panels(img: np.ndarray, max_items: int = 4) -> List[Detection]:
    """
    Classical PV detector: photovoltaic modules are dark, blue-to-black, and carry a
    regular cell grid. Candidate regions come from an HSV colour mask; each must be
    large, compact (high solidity) and show dense straight edges inside (the grid).
    """
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    mask = (((hue >= 95) & (hue <= 130) & (sat >= 35)) | (sat < 60)) & (val >= 20) & (val <= 120)
    mask = mask.astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    edges = cv2.Canny(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 30, 90)
    blue_share_min = 0.35

    out: List[Detection] = []
    img_area = float(h * w)
    for c in contours:
        area = cv2.contourArea(c)
        if area < 0.01 * img_area:
            continue
        hull_area = cv2.contourArea(cv2.convexHull(c))
        solidity = area / hull_area if hull_area else 0.0
        if solidity < 0.8:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        region = np.zeros((h, w), np.uint8)
        cv2.drawContours(region, [c], -1, 255, -1)
        inside = region > 0
        blue_share = float(((hue >= 95) & (hue <= 130) & (sat >= 35))[inside].mean())
        edge_density = float((edges[inside] > 0).mean())
        if blue_share < blue_share_min or edge_density < 0.04:
            continue
        conf = min(0.9, 0.35 + 0.4 * blue_share + 2.0 * min(edge_density, 0.15))
        poly = cv2.approxPolyDP(c, 0.01 * cv2.arcLength(c, True), True).reshape(-1, 2)
        out.append(
            Detection(
                label="Solar panel",
                confidence=round(conf, 3),
                box=_norm_box(x, y, x + bw, y + bh, w, h),
                source="opencv-pv-texture",
                polygon=[(round(px / w, 4), round(py / h, 4)) for px, py in poly.tolist()],
                extra={"blue_share": round(blue_share, 3), "grid_edge_density": round(edge_density, 3)},
            )
        )
    out.sort(key=lambda d: d.confidence, reverse=True)
    return out[:max_items]


# ======================================================================
# Thermal heat-loss segmentation
# ======================================================================

def heat_index(img: np.ndarray) -> np.ndarray:
    """
    Relative temperature proxy in 0-1 for a false-colour thermal image.
    Ironbow / rainbow palettes run dark-blue/purple (cold) -> red -> yellow -> white (hot),
    so warmth combines brightness with a hue score that rises from blue towards yellow.
    Greyscale thermal images reduce to brightness.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hue, sat, val = hsv[..., 0] * 2.0, hsv[..., 1] / 255.0, hsv[..., 2] / 255.0  # hue in degrees
    # Map hue to warmth: 240-300 deg (blue/purple) cold -> 0 deg (red) -> 60 deg (yellow) hot.
    rotated = np.where(hue > 200, hue - 360.0, hue)  # purple/blue become negative
    hue_warmth = np.clip((rotated + 120.0) / 180.0, 0.0, 1.0)
    colour = sat > 0.25
    idx = np.where(colour, 0.55 * hue_warmth + 0.45 * val, val)
    idx = cv2.GaussianBlur(idx, (0, 0), 2.0)
    lo, hi = np.percentile(idx, 1), np.percentile(idx, 99.5)
    if hi - lo < MIN_THERMAL_CONTRAST:
        # A near-uniform frame has no hotspots; stretching it would invent them.
        return np.zeros_like(idx)
    return np.clip((idx - lo) / (hi - lo), 0.0, 1.0)


def segment_hotspots(img: np.ndarray, max_items: int = 12) -> Tuple[List[Detection], Dict]:
    h, w = img.shape[:2]
    idx = heat_index(img)
    idx8 = (idx * 255).astype(np.uint8)
    otsu, _ = cv2.threshold(idx8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Only the warm tail counts as loss: at least the 80th percentile and above Otsu.
    thr = max(otsu, float(np.percentile(idx8, 80)), 1.0)
    mask = (idx8 >= thr).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    img_area = float(h * w)
    regions = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < 0.003 * img_area:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        region_mask = np.zeros((h, w), np.uint8)
        cv2.drawContours(region_mask, [c], -1, 255, -1)
        intensity = float(idx[region_mask > 0].mean())
        poly = cv2.approxPolyDP(c, 0.006 * cv2.arcLength(c, True), True).reshape(-1, 2)
        m = cv2.moments(c)
        cy = (m["m01"] / m["m00"]) / h if m["m00"] else (y + bh / 2) / h
        rectangularity = area / float(bw * bh)
        aspect = bw / float(bh)
        if rectangularity > 0.72 and 0.35 <= aspect <= 2.8 and area < 0.06 * img_area:
            zone = "window / door"  # compact rectangular hotspot: glazing or a door
        elif cy < 0.33:
            zone = "roof line"
        elif cy > 0.78:
            zone = "floor line"
        else:
            zone = "wall"
        regions.append((area, intensity, x, y, bw, bh, poly, zone))

    regions.sort(key=lambda r: r[0] * r[1], reverse=True)
    detections = []
    for area, intensity, x, y, bw, bh, poly, zone in regions[:max_items]:
        detections.append(
            Detection(
                label=f"Hotspot · {zone}",
                confidence=round(min(0.95, 0.4 + 0.55 * intensity), 3),
                box=_norm_box(x, y, x + bw, y + bh, w, h),
                source="thermal-segmentation",
                polygon=[(round(px / w, 4), round(py / h, 4)) for px, py in poly.tolist()],
                extra={"zone": zone, "area_pct": round(100 * area / img_area, 2), "intensity": round(intensity, 3)},
            )
        )

    hot_pct = 100.0 * float((mask > 0).mean())
    # Heat-loss index: share of the frame that is hot, weighted by how hot it is.
    mean_hot = float(idx[mask > 0].mean()) if hot_pct > 0 else 0.0
    loss_index = int(round(min(100.0, hot_pct * 2.2 * (0.5 + mean_hot))))
    stats = {
        "hot_area_pct": round(hot_pct, 2),
        "mean_hot_intensity": round(mean_hot, 3),
        "heat_loss_index": loss_index,
        "threshold": int(thr),
    }
    return detections, stats


# ======================================================================
# Suggestion rules (deterministic, auditable)
# ======================================================================

def _exterior_suggestions(dets: List[Detection]) -> List[Suggestion]:
    out: List[Suggestion] = []
    by_label: Dict[str, List[int]] = {}
    for i, d in enumerate(dets):
        by_label.setdefault(d.label, []).append(i)

    def best(label):
        ids = by_label.get(label, [])
        return max((dets[i].confidence for i in ids), default=0.0), ids

    conf, ids = best("Solar panel")
    if ids:
        out.append(Suggestion("already_installed", "Solar PV looks installed",
                              f"{len(ids)} solar panel region(s) detected on the roof.", conf, "solar_pv", ids))
    else:
        roof_conf, roof_ids = best("Roof")
        out.append(Suggestion("add_measure", "Consider rooftop solar PV",
                              "No solar panels were detected" + (" on the visible roof." if roof_ids else "."),
                              0.45 if roof_ids else 0.3, "solar_pv", roof_ids))

    conf, ids = best("Outdoor heat-pump / AC unit")
    if ids:
        out.append(Suggestion("already_installed", "Outdoor heat-pump unit spotted",
                              "An outdoor unit is visible. Check whether it is an air-source heat pump.",
                              conf, "heat_pump", ids))

    w_conf, w_ids = best("Window")
    if w_ids:
        out.append(Suggestion("observation", f"{len(w_ids)} window(s) detected",
                              "Glazing type can't be read reliably from a photo. Confirm it in the property details.",
                              w_conf, None, w_ids))
        if len(w_ids) >= 6:
            out.append(Suggestion("add_measure", "Large glazed area: check window upgrade",
                                  f"{len(w_ids)} openings on this elevation. Single or old double glazing here loses a lot of heat.",
                                  min(0.7, w_conf), "window_upgrade", w_ids))
        out.append(Suggestion("add_measure", "Draught-proof doors and windows",
                              "Cheap, fast, and a hard prerequisite for a heat pump in the planner.",
                              0.5, "draught_proofing", w_ids + by_label.get("Door", [])))

    b_conf, b_ids = best("Basement opening")
    if b_ids:
        out.append(Suggestion("add_measure", "Basement openings: check the floor",
                              "Low facade openings suggest a basement or suspended floor. Insulating it cuts heat lost downwards.",
                              min(0.6, b_conf), "floor_insulation", b_ids))
    g_conf, g_ids = best("Garage door")
    if g_ids:
        out.append(Suggestion("observation", "Integral garage spotted",
                              "Walls and ceilings next to an unheated garage lose heat. Check they are insulated.",
                              g_conf, None, g_ids))

    c_conf, c_ids = best("Chimney")
    if c_ids:
        out.append(Suggestion("add_measure", "Chimney found: seal unused flues",
                              "Open flues are a common draught path. Draught-proofing covers chimney balloons and sealing.",
                              c_conf, "draught_proofing", c_ids))
    return out


def _aerial_suggestions(dets: List[Detection], pv_area_pct: float) -> List[Suggestion]:
    ids = [i for i, d in enumerate(dets) if d.label == "Solar panel"]
    if ids:
        conf = max(dets[i].confidence for i in ids)
        return [Suggestion("already_installed", "Rooftop solar PV detected",
                           f"{len(ids)} array(s) cover about {pv_area_pct:.1f}% of the image from above.", conf, "solar_pv", ids)]
    return [Suggestion("add_measure", "No rooftop solar seen from above",
                       "No PV arrays were found on the roofs in this image. Consider solar PV.", 0.4, "solar_pv", [])]


def _dedupe(dets: List[Detection], iou_thr: float, contained: float = 0.7) -> List[Detection]:
    """Greedy NMS across detectors: keep the most confident of overlapping boxes, and drop a
    box that lies mostly inside an already-kept one (a part of the same object)."""
    kept: List[Detection] = []
    for d in sorted(dets, key=lambda x: x.confidence, reverse=True):
        if all(_iou(d.box, k.box) <= iou_thr and _overlap_fraction(d.box, k.box) <= contained for k in kept):
            kept.append(d)
    return kept


def _thermal_suggestions(dets: List[Detection], stats: Dict, windows: List[Detection]) -> List[Suggestion]:
    out: List[Suggestion] = []
    zones: Dict[str, List[int]] = {}
    for i, d in enumerate(dets):
        zones.setdefault(d.extra.get("zone", "wall"), []).append(i)

    window_hits = [
        i for i, d in enumerate(dets)
        if d.extra.get("zone") == "window / door" or any(_overlap_fraction(d.box, wdet.box) > 0.35 or _overlap_fraction(wdet.box, d.box) > 0.35 for wdet in windows)
    ]

    if zones.get("roof line"):
        ids = zones["roof line"]
        out.append(Suggestion("add_measure", "Heat escaping at the roof line",
                              "Warm regions along the top of the building point to thin loft insulation.",
                              max(dets[i].confidence for i in ids), "loft_insulation", ids))
    wall_ids = [i for i in zones.get("wall", []) if i not in window_hits]
    wall_area = sum(dets[i].extra["area_pct"] for i in wall_ids)
    if wall_ids and wall_area >= 4:
        out.append(Suggestion("add_measure", "Diffuse heat loss through the walls",
                              f"Hot regions cover about {wall_area:.0f}% of the frame away from windows, typical of uninsulated walls.",
                              max(dets[i].confidence for i in wall_ids), "wall_insulation", wall_ids))
    if window_hits:
        out.append(Suggestion("add_measure", "Windows and frames are leaking heat",
                              f"{len(window_hits)} hotspot(s) line up with windows or doors.",
                              max(dets[i].confidence for i in window_hits), "window_upgrade", window_hits))
        out.append(Suggestion("add_measure", "Draught-proof around openings",
                              "Hot edges around frames usually mean air leakage.", 0.6, "draught_proofing", window_hits))
    if zones.get("floor line"):
        ids = zones["floor line"]
        out.append(Suggestion("add_measure", "Heat loss near the floor line",
                              "Warm bands at ground level suggest an uninsulated suspended floor.",
                              max(dets[i].confidence for i in ids), "floor_insulation", ids))
    if not dets:
        out.append(Suggestion("observation", "No significant hotspots",
                              "The thermal image is fairly uniform. Check it was taken on a cold day with the heating on.",
                              0.5))
    return out


# ======================================================================
# Entry point
# ======================================================================

def _detect_aerial_solar(img: np.ndarray) -> Tuple[Optional[List[Detection]], str]:
    """Use YOLOv8 when the preferred YOLO26 checkpoint cannot load or predict."""
    detector = solar_yolo
    for current in (detector, solar_yolov8) if detector is solar_yolo26 else (detector,):
        if current is solar_yolov8 and detector is solar_yolo26:
            current.load()
        if current.error:
            continue
        try:
            detections = current.detect(img)
        except Exception as exc:
            current.error = f"{type(exc).__name__}: {exc}"[:300]
            logger.warning("%s inference failed: %s", current.path.name, current.error)
            continue
        if detections is not None:
            return detections, current.source
        if current is solar_yolo26 and not current.error:
            break  # still warming up; use the OpenCV fallback for this request
    return None, "opencv-pv-texture"


def analyze_image(data: bytes, mode: str = "auto", live: bool = False) -> Dict:
    """
    live=True is the camera path (about one frame a second): only the trained detectors at
    640 px, no YOLO-World, OpenCV extras or SAM, so a frame comes back in a fraction of a second.
    """
    t0 = time.perf_counter()
    img = decode_image(data)
    quality = image_quality(img)
    if mode not in {"auto", "exterior", "thermal", "aerial"}:
        raise ValueError("mode must be auto, exterior, thermal or aerial")
    resolved = ("thermal" if looks_thermal(img) else "exterior") if mode == "auto" else mode

    engines_used: List[str] = []
    notes: List[str] = []
    stats: Dict = {}

    if resolved == "aerial":
        dets, solar_source = _detect_aerial_solar(img)
        if dets is None:
            dets = detect_solar_panels(img, max_items=12)
            engines_used.append("opencv-pv-texture")
            notes.append("Trained solar model unavailable, so the classical PV texture detector was used.")
        else:
            engines_used.append(solar_source)
        dets = _dedupe(dets, 0.5)
        dets.sort(key=lambda d: d.confidence, reverse=True)
        pv_area = 100.0 * sum((d.box[2] - d.box[0]) * (d.box[3] - d.box[1]) for d in dets)
        stats = {"pv_area_pct": round(pv_area, 2), "pv_arrays": len(dets)}
        suggestions = _aerial_suggestions(dets, pv_area)
    elif resolved == "exterior" and live:
        fw, fo = facade_windows.detect(img), facade_openings.detect(img)
        dets = _dedupe((fw or []) + (fo or []), 0.2)
        if fw is not None or fo is not None:
            engines_used.append("facade-yolov8")
        else:
            notes.append("Detectors are still loading; try again in a moment.")
        dets.sort(key=lambda d: d.confidence, reverse=True)
        suggestions = _exterior_suggestions(dets)
    elif resolved == "exterior":
        dets: List[Detection] = []
        fw, fo = facade_windows.detect(img), facade_openings.detect(img)
        trained_openings = fw is not None or fo is not None
        if trained_openings:
            engines_used.append("facade-yolov8")
            # Facade repo rule: a window and a door overlapping by IoU > 0.2 keep the more confident one.
            dets += _dedupe((fw or []) + (fo or []), 0.2)
        yw = yolo_world.detect(img)
        if yw is not None:
            engines_used.append("yolo-world")
            covered = {"Window", "Door", "Garage door"} if trained_openings else set()
            dets += [d for d in yw if d.label not in covered]
        if not any(d.label == "Window" for d in dets):
            dets += detect_openings_geometric(img, max_items=12)
            engines_used.append("opencv-geometry")
            if yw is None and not trained_openings:
                notes.append("Neural detectors unavailable, so only window-like openings were found with classical CV.")
        # The trained solar model was fitted on top-down satellite tiles; on street-level or
        # oblique photos it fires on dark rectangles (planters, cars), so it runs in aerial mode only.
        pv: List[Detection] = []
        texture = detect_solar_panels(img)
        if texture:
            pv += texture
            engines_used.append("opencv-pv-texture")
        others = [d for d in dets if d.label != "Solar panel"]
        solar = _dedupe([d for d in dets if d.label == "Solar panel"] + pv, 0.3)
        dets = others + solar
        dets.sort(key=lambda d: d.confidence, reverse=True)
        suggestions = _exterior_suggestions(dets)
    else:
        dets, stats = segment_hotspots(img)
        engines_used.append("thermal-segmentation")
        # Windows found in the thermal frame let us tell glazing losses from wall losses.
        windows = [d for d in (facade_windows.detect(img) or []) + (facade_openings.detect(img) or []) if d.label in ("Window", "Door")]
        if windows:
            engines_used.append("facade-yolov8")
        elif not live:
            windows = [d for d in (yolo_world.detect(img) or []) if d.label in ("Window", "Door")]
            if windows:
                engines_used.append("yolo-world")
        if not windows and not live:
            windows = detect_openings_geometric(img, max_items=12)
        suggestions = _thermal_suggestions(dets, stats, windows)
        offset = len(dets)
        dets = dets + windows
        for s in suggestions:
            s.evidence = [i for i in s.evidence if i < offset]

    counts: Dict[str, int] = {}
    for d in dets:
        counts[d.label] = counts.get(d.label, 0) + 1

    return {
        "mode": resolved,
        "mode_requested": mode,
        "live": live,
        "detections": [
            {
                "id": i,
                "label": d.label,
                "confidence": d.confidence,
                "box": list(d.box),
                "source": d.source,
                "polygon": [list(p) for p in d.polygon] if d.polygon else None,
                **({"extra": d.extra} if d.extra else {}),
            }
            for i, d in enumerate(dets)
        ],
        "counts": counts,
        "suggestions": [s.__dict__ for s in suggestions],
        "thermal": stats if resolved == "thermal" else None,
        "aerial": stats if resolved == "aerial" else None,
        "segmentable": [] if live else [i for i, d in enumerate(dets) if d.label in SEGMENTABLE and not d.polygon][:MAX_SEGMENT_BOXES],
        "quality": quality,
        "engines_used": engines_used,
        "notes": notes,
        "processing_ms": int((time.perf_counter() - t0) * 1000),
        "image_stored": False,
    }


def segment_image(data: bytes, boxes: List[List[float]]) -> Dict:
    """
    SAM masks for detection boxes (normalised [x1, y1, x2, y2]). The image is re-sent by the
    browser rather than kept on the server, so nothing is stored between calls.
    """
    from backend.engine.sam_segmenter import sam

    t0 = time.perf_counter()
    if not boxes:
        raise ValueError("No boxes to segment.")
    if len(boxes) > MAX_SEGMENT_BOXES:
        raise ValueError(f"At most {MAX_SEGMENT_BOXES} boxes per request.")
    for b in boxes:
        if (
            not isinstance(b, (list, tuple))
            or len(b) != 4
            or not all(type(v) in (int, float) and 0.0 <= v <= 1.0 for v in b)
            or b[0] >= b[2]
            or b[1] >= b[3]
        ):
            raise ValueError("Each box must be [x1, y1, x2, y2] with 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1.")
    if sam.load() is None:
        return {"available": False, "reason": sam.error or "SAM is still loading.", "masks": [None] * len(boxes)}
    img = decode_image(data)
    h, w = img.shape[:2]
    px = [[b[0] * w, b[1] * h, b[2] * w, b[3] * h] for b in boxes]
    masks = sam.segment(img, px)
    return {
        "available": True,
        "masks": [
            None if m is None else {"polygon": [list(p) for p in m["polygon"]], "area_pct": round(100 * m["area_frac"], 3), "score": m["score"]}
            for m in masks
        ],
        "processing_ms": int((time.perf_counter() - t0) * 1000),
        "model": "segment-anything ViT-B",
        "image_stored": False,
    }

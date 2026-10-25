"""
Vision service inference layer (pattern from Alex-Lekov/yolov8-fastapi: a thin FastAPI shell
around an image-bytes -> JSON predictor; upload / preprocessing pattern from
WelkinU/yolov5-fastapi-demo).

Preprocessing (decode, header check against decompression bombs, format allowlist, downscale,
quality metrics, thermal-palette detection) lives in backend.engine.vision_engine and runs
before any model sees the pixels. Models: trained facade YOLOv8 (windows / doors / basements /
garages), YOLO-World (open vocabulary), satellite solar YOLOv8, SAM ViT-B; all loaded with the
weights-only safe loader.

Glazing type (single / double / triple) is NOT predicted: no trained glazing-type classifier
exists in the source repositories, and guessing it from an RGB photo would be unreliable. The
response says so explicitly instead of returning a fabricated label.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List

from backend.engine import vision_engine
from backend.engine.safe_models import INFERENCE_EXECUTOR

GLAZING_TYPE_NOTE = (
    "Glazing type (single / double / triple) is not inferred from photos: no trained classifier "
    "is available. Confirm it in the home's details."
)


async def detect(data: bytes, mode: str, live: bool = False) -> Dict:
    result = await asyncio.wrap_future(INFERENCE_EXECUTOR.submit(vision_engine.analyze_image, data, mode, live))
    result["glazing_type"] = {"predicted": None, "note": GLAZING_TYPE_NOTE}
    return result


async def segment(data: bytes, boxes: List[List[float]]) -> Dict:
    return await asyncio.wrap_future(INFERENCE_EXECUTOR.submit(vision_engine.segment_image, data, boxes))


def status() -> Dict:
    return vision_engine.vision_status()


def warm_up() -> None:
    vision_engine.warm_up_async()

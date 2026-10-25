"""
Gateway vision routes. Inference runs in the vision microservice when VISION_SERVICE_URL is set
(Docker Compose); otherwise in-process on the single torch inference thread.
"""

import asyncio
import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from backend.engine import vision_engine
from backend.engine.safe_models import INFERENCE_EXECUTOR
from backend.services import remote

router = APIRouter(prefix="/vision", tags=["Computer Vision"])


def _redact_errors(value):
    if isinstance(value, dict):
        return {k: (bool(v) if k == "error" else _redact_errors(v)) for k, v in value.items()}
    return value


async def _run(fn, *args):
    if remote.remote_enabled("VISION_SERVICE_URL"):
        return await run_in_threadpool(fn, *args)          # network I/O: don't hold the torch thread
    return await asyncio.wrap_future(INFERENCE_EXECUTOR.submit(fn, *args))


async def _read(file: UploadFile) -> bytes:
    data = await file.read(vision_engine.MAX_UPLOAD_BYTES + 1)
    if len(data) > vision_engine.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Image is larger than 12 MB.")
    return data


@router.get("/status")
async def vision_status():
    """Which detectors are loaded. Photos are never stored. Error details stay in server logs."""
    return _redact_errors(await run_in_threadpool(remote.vision_status))


@router.post("/analyze")
async def vision_analyze(
    file: UploadFile = File(..., description="JPG, PNG or WebP photo of the home"),
    mode: str = Form("auto", description="auto | exterior | thermal | aerial"),
):
    """
    Detects retrofit evidence in a photo: facade YOLOv8 + YOLO-World objects for exteriors,
    heat-loss hotspot segmentation for thermal images, rooftop PV for aerial images. Returns
    suggestions only; the planner changes after the user confirms them. Not stored.
    """
    data = await _read(file)
    try:
        return await _run(remote.vision_analyze, data, file.filename, file.content_type, mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/live")
async def vision_live(
    file: UploadFile = File(..., description="One JPEG frame from the camera (max 2 MB)"),
    mode: str = Form("exterior", description="exterior | thermal | aerial"),
):
    """
    Live camera: analyses one frame on the fast path (trained detectors at 640 px), so the
    browser can show boxes over the video about once a second. Frames are never stored.
    """
    data = await file.read(vision_engine.MAX_LIVE_BYTES + 1)
    if len(data) > vision_engine.MAX_LIVE_BYTES:
        raise HTTPException(413, "Camera frame is larger than 2 MB.")
    try:
        return await _run(remote.vision_analyze, data, file.filename, file.content_type, mode, True)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/segment")
async def vision_segment(
    file: UploadFile = File(..., description="The same photo that was analysed"),
    boxes: str = Form(..., description="JSON list of [x1, y1, x2, y2] boxes, normalised 0-1"),
):
    """Segment Anything (ViT-B) outlines for detection boxes. The image is not stored."""
    try:
        parsed = json.loads(boxes)
        if not isinstance(parsed, list):
            raise ValueError("boxes must be a JSON list")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(422, f"Invalid boxes: {exc}")
    data = await _read(file)
    try:
        return await _run(remote.vision_segment, data, file.filename, file.content_type, parsed)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

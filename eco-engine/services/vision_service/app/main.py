"""
Vision microservice: POST /detect, POST /segment, GET /status.
Internal only (service token); the gateway is the only public entry point.
Photos are processed in memory and never stored.
"""

import json
from contextlib import asynccontextmanager

from fastapi import File, Form, HTTPException, UploadFile

from backend.engine import vision_engine
from services.common.app_factory import ServiceAuth, create_service_app
from services.vision_service.app import inference


@asynccontextmanager
async def lifespan(app):
    inference.warm_up()  # load YOLO / SAM in the background so the first request is fast
    yield


app = create_service_app(
    "Eco vision service",
    "Facade, thermal and aerial computer vision: YOLOv8 (trained facade + solar), YOLO-World, SAM ViT-B.",
    lifespan=lifespan,
    upload_paths=("/detect", "/segment"),
)


async def _read(file: UploadFile) -> bytes:
    data = await file.read(vision_engine.MAX_UPLOAD_BYTES + 1)
    if len(data) > vision_engine.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Image is larger than 12 MB.")
    return data


@app.post("/detect", dependencies=[ServiceAuth])
async def detect(
    file: UploadFile = File(..., description="JPG, PNG or WebP photo"),
    mode: str = Form("auto", description="auto | exterior | thermal | aerial"),
    live: bool = Form(False, description="Camera frame: fast path, trained detectors only"),
):
    """Object detection + thermal / aerial analysis. Returns bounding boxes, polygons and suggestions."""
    data = await _read(file)
    if live and len(data) > vision_engine.MAX_LIVE_BYTES:
        raise HTTPException(413, "Camera frame is larger than 2 MB.")
    try:
        return await inference.detect(data, mode, live)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/segment", dependencies=[ServiceAuth])
async def segment(file: UploadFile = File(...), boxes: str = Form(...)):
    """Segment Anything masks for normalised [x1, y1, x2, y2] boxes."""
    try:
        parsed = json.loads(boxes)
        if not isinstance(parsed, list):
            raise ValueError("boxes must be a JSON list")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(422, f"Invalid boxes: {exc}")
    data = await _read(file)
    try:
        return await inference.segment(data, parsed)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/status", dependencies=[ServiceAuth])
def status():
    return inference.status()

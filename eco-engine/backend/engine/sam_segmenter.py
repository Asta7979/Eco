"""
Segment Anything (Meta, ViT-B) box-prompted segmentation.

Follows the reference pipeline from facebookresearch/segment-anything (as used by
"Detection of low facade openings using YOLOv8 and SAM"): a detector proposes boxes,
SAM turns each box into a pixel mask. The image is encoded once and every box is
decoded in one batch, so N detections cost one encoder pass.

Weights: models/sam_vit_b_01ec64.pth, loaded tensor-only via safe_models.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import cv2
import numpy as np

from backend.engine.safe_models import MODEL_LOAD_LOCK, load_state_dict, use_all_cores

logger = logging.getLogger(__name__)

SAM_PATH = Path(os.getenv("ECO_SAM_MODEL", Path(__file__).resolve().parent.parent.parent / "models" / "sam_vit_b_01ec64.pth"))
SAM_INPUT = 1024
MAX_BOXES = 24


class SamSegmenter:
    def __init__(self, path: Path = SAM_PATH):
        self.path = path
        self._model = None
        self._lock = threading.Lock()
        self._predict_lock = threading.Lock()
        self._loading = False
        self.error: Optional[str] = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def status(self) -> Dict:
        return {"file_present": self.path.exists(), "loaded": self.ready, "loading": self._loading, "error": self.error}

    def load(self):
        if self._model is not None or self.error:
            return self._model
        with MODEL_LOAD_LOCK:
            if self._model is not None or self.error:
                return self._model
            if not self.path.exists():
                self.error = f"SAM weights not found at {self.path.name}."
                return None
            self._loading = True
            try:
                import torch
                from ultralytics.models.sam.build import build_sam_vit_b

                model = build_sam_vit_b(checkpoint=None)
                missing, unexpected = model.load_state_dict(load_state_dict(self.path), strict=False)
                if missing or unexpected:
                    raise RuntimeError(f"weights do not match SAM ViT-B ({len(missing)} missing, {len(unexpected)} unexpected)")
                model.eval()
                torch.set_num_threads(max(1, (os.cpu_count() or 2) - 1))
                self._model = model
                logger.info("SAM ViT-B loaded")
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"[:300]
                logger.warning("SAM unavailable: %s", self.error)
            finally:
                self._loading = False
        return self._model

    def segment(self, img_bgr: np.ndarray, boxes_px: Sequence[Sequence[float]]) -> List[Optional[Dict]]:
        """
        Returns, for each box, {"polygon": [(x, y) normalised], "area_frac": float, "score": float}
        or None when SAM produced no usable mask. Boxes are pixel [x1, y1, x2, y2].
        """
        model = self._model
        if model is None or not len(boxes_px):
            return [None] * len(boxes_px)
        import torch
        import torch.nn.functional as F

        h, w = img_bgr.shape[:2]
        scale = SAM_INPUT / max(h, w)
        nh, nw = int(round(h * scale)), int(round(w * scale))
        rgb = cv2.cvtColor(cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)

        with self._predict_lock, torch.inference_mode():
            use_all_cores()
            x = torch.from_numpy(rgb).permute(2, 0, 1).float()
            x = (x - model.pixel_mean.view(3, 1, 1)) / model.pixel_std.view(3, 1, 1)
            x = F.pad(x, (0, SAM_INPUT - nw, 0, SAM_INPUT - nh)).unsqueeze(0)
            emb = model.image_encoder(x)

            boxes = torch.tensor(np.asarray(boxes_px, dtype=np.float32) * scale)
            sparse, dense = model.prompt_encoder(points=None, boxes=boxes, masks=None)
            low_res, iou = model.mask_decoder(
                image_embeddings=emb,
                image_pe=model.prompt_encoder.get_dense_pe(),
                sparse_prompt_embeddings=sparse,
                dense_prompt_embeddings=dense,
                multimask_output=False,
            )
            masks = F.interpolate(low_res, (SAM_INPUT, SAM_INPUT), mode="bilinear", align_corners=False)
            masks = masks[..., :nh, :nw]
            masks = F.interpolate(masks, (h, w), mode="bilinear", align_corners=False)
            binary = (masks[:, 0] > 0.0).numpy().astype(np.uint8)
            scores = iou[:, 0].tolist()

        out: List[Optional[Dict]] = []
        for m, score in zip(binary, scores):
            contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                out.append(None)
                continue
            c = max(contours, key=cv2.contourArea)
            if cv2.contourArea(c) < 12:
                out.append(None)
                continue
            poly = cv2.approxPolyDP(c, 0.004 * cv2.arcLength(c, True), True).reshape(-1, 2)
            out.append({
                "polygon": [(round(px / w, 4), round(py / h, 4)) for px, py in poly.tolist()],
                "area_frac": float(m.sum()) / float(h * w),
                "score": round(float(score), 3),
            })
        return out


sam = SamSegmenter()

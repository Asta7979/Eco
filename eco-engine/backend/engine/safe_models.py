"""
Safe loading for third-party model files.

Pickle-based weights (.pt / .pth / .pkl) can execute arbitrary code when loaded with the
default loaders (Ultralytics forces torch.load(weights_only=False)). Every file here is
loaded without that risk:

* SAM (Meta, ViT-B)       torch.load(weights_only=True): plain tensors only.
* YOLOv8 checkpoints       torch.load(weights_only=True) with an allowlist built from the
                           exact torch / ultralytics classes the checkpoint declares. Any
                           other global (os, subprocess, eval, ...) makes loading fail.
* XGBoost (EPC predictor)  the pickle is never unpickled: the raw booster bytes are read
                           with pickletools and loaded in XGBoost's native format.

Third-party models used (see README for sources and licences):
    models/sam_vit_b_01ec64.pth    facebookresearch/segment-anything
    models/facade_windows.pt       elaounijassim/Detection-of-low-facade-openings-using-YOLOv8-and-SAM
    models/facade_openings.pt      (same repo: door / basement / garage)
    models/solar_panel_yolov8.pt   ArielDrabkin/Solar-Panel-Detector (final mosaic-augmentation model)
    models/epc/xgb_model.pkl       b-rasagna/EPC-Energy-Rating-Prediction
"""

from __future__ import annotations

import importlib
import io
import pickletools
import zipfile
from pathlib import Path
from typing import List, Set

ALLOWED_GLOBAL_ROOTS = {"torch", "ultralytics", "collections", "__builtin__", "builtins", "numpy", "_codecs"}
ALLOWED_BUILTINS = {"set", "frozenset", "dict", "list", "tuple", "bytearray", "slice"}
FORBIDDEN_NAMES = {"eval", "exec", "compile", "open", "__import__", "system", "popen"}
LEGACY_ULTRALYTICS_PATHS = [("ultralytics.yolo.utils.", "ultralytics.utils.")]


# torch / ultralytics imports are not thread-safe: loading two models at once from
# different threads can deadlock inside torch._inductor. Every loader takes this lock.
import threading

MODEL_LOAD_LOCK = threading.RLock()


def use_all_cores() -> None:
    """
    Ultralytics sets OMP_NUM_THREADS=1 on import, and OpenMP thread counts are per thread:
    a server worker thread reports torch.get_num_threads() == N but still runs on one core
    (SAM: 38 s instead of 9 s). Re-applying the count in the calling thread fixes it.
    """
    import os

    try:
        import torch
    except ImportError:  # gateway-only installs (e.g. Vercel) have no torch: nothing to tune
        return

    torch.set_num_threads(max(1, (os.cpu_count() or 2) - 1))


# All torch work (model loading and inference) runs on this one thread. Running it from
# many server worker threads gives each its own OpenMP team, and the teams compete for
# the cores: SAM went from 7 s to 40 s. One thread keeps a single, warm team.
from concurrent.futures import ThreadPoolExecutor

INFERENCE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="torch-inference", initializer=use_all_cores)


class UnsafeModelError(RuntimeError):
    pass


def pickle_globals(data: bytes) -> Set[str]:
    """Every module.name a pickle would import, found without executing it."""
    found, strings = set(), []
    for op, arg, _ in pickletools.genops(io.BytesIO(data)):
        if isinstance(arg, str):
            strings.append(arg)
        if op.name == "GLOBAL":
            found.add(arg.replace(" ", "."))
        elif op.name == "STACK_GLOBAL" and len(strings) >= 2:
            found.add(f"{strings[-2]}.{strings[-1]}")
    return found


def _checkpoint_globals(path: Path) -> Set[str]:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.endswith(".pkl")]
        out: Set[str] = set()
        for n in names:
            out |= pickle_globals(z.read(n))
    return out


def audit_checkpoint(path: Path) -> List[str]:
    """Raises UnsafeModelError unless every global is a known-safe torch/ultralytics name."""
    globs = _checkpoint_globals(path)
    for g in globs:
        root, _, name = g.partition(".")
        leaf = g.rsplit(".", 1)[-1]
        if root not in ALLOWED_GLOBAL_ROOTS or leaf in FORBIDDEN_NAMES:
            raise UnsafeModelError(f"{path.name}: refuses to load, it references {g}")
        if root in ("__builtin__", "builtins") and name not in ALLOWED_BUILTINS:
            raise UnsafeModelError(f"{path.name}: refuses to load, it references {g}")
    return sorted(globs)


def _resolve(dotted: str):
    module, _, attr = dotted.rpartition(".")
    if module in ("__builtin__",):
        module = "builtins"
    obj = importlib.import_module(module)
    return getattr(obj, attr)


def load_state_dict(path: Path):
    """Tensor-only checkpoint (e.g. SAM)."""
    import torch

    audit_checkpoint(path)
    return torch.load(str(path), map_location="cpu", weights_only=True)


def _load_checkpoint_module(path: Path):
    """
    Unpickles an Ultralytics checkpoint through torch's weights-only unpickler, allowing only
    the classes the file declares (all audited to be torch / ultralytics layers), and returns
    its network in eval mode.
    """
    import torch

    allowed = []
    for g in audit_checkpoint(path):
        try:
            allowed.append(_resolve(g))
        except (ImportError, AttributeError):
            # Checkpoints saved with ultralytics 8.0.x name classes under ultralytics.yolo.*;
            # map each one to the same class at its current path (exact rename only).
            for old, new in LEGACY_ULTRALYTICS_PATHS:
                if g.startswith(old):
                    try:
                        allowed.append((_resolve(new + g[len(old):]), g))
                    except (ImportError, AttributeError):
                        pass  # weights_only will reject it explicitly
                    break
    with torch.serialization.safe_globals(allowed):
        ckpt = torch.load(str(path), map_location="cpu", weights_only=True)
    net = (ckpt.get("ema") or ckpt["model"]).float().eval()
    for p in net.parameters():
        p.requires_grad_(False)
    return net


def load_yolo(path: Path):
    """A ready-to-predict ultralytics.YOLO detector from a checkpoint (YOLOv8 or YOLO26)."""
    from ultralytics import YOLO

    yolo = YOLO("yolov8n.yaml", task="detect", verbose=False)  # architecture shell, replaced below
    yolo.model = _load_checkpoint_module(path)
    yolo.task = "detect"
    yolo.ckpt = None
    yolo.ckpt_path = str(path)
    yolo.overrides = {"task": "detect", "imgsz": 640}
    return yolo


def load_xgb_booster_from_pickle(path: Path):
    """Reads the raw XGBoost model bytes out of a pickled XGBClassifier without unpickling it."""
    import xgboost as xgb

    data = path.read_bytes()
    globs = pickle_globals(data)
    unexpected = [g for g in globs if not (g.startswith("xgboost.") or g == "builtins.bytearray")]
    if unexpected:
        raise UnsafeModelError(f"{path.name}: unexpected globals {unexpected}")
    blobs = [arg for op, arg, _ in pickletools.genops(io.BytesIO(data)) if isinstance(arg, (bytes, bytearray)) and len(arg) > 1000]
    if not blobs:
        raise UnsafeModelError(f"{path.name}: no booster payload found")
    # The pickled payload is XGBoost's own serialisation (model + config); hand the bytes to
    # its C-level unserialiser exactly as Booster.__setstate__ does, with no Python unpickling.
    booster = xgb.Booster.__new__(xgb.Booster)
    booster.__setstate__({"handle": bytearray(max(blobs, key=len))})
    return booster


def pickled_string_list(path: Path) -> List[str]:
    """A pickled list of strings (feature names), read without unpickling."""
    data = path.read_bytes()
    if pickle_globals(data):
        raise UnsafeModelError(f"{path.name}: expected plain data")
    return [arg for op, arg, _ in pickletools.genops(io.BytesIO(data)) if isinstance(arg, str)]

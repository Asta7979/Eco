"""
Downloads the public Roboflow Universe datasets used to train the YOLO26 detectors.

    python training/download_datasets.py

Needs ROBOFLOW_API_KEY in eco-engine/.env. Datasets land in training/datasets/ (not committed).
Both are CC BY 4.0; credit the authors when publishing (see training/README.md).
"""

import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "datasets"

# name -> (workspace/project, version). Formats are exported as YOLOv8 (same layout YOLO26 trains on).
DATASETS = {
    "windows": ("gautam-parmar-2u4ak/windowditection", 1),   # 871 facade photos, 15,199 windows
    "solar": ("rooftop-jcnyk/solar-plane", 1),               # 305 aerial images, 3,680 panels
}


def api_key() -> str:
    for line in (HERE.parent / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("ROBOFLOW_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    sys.exit("ROBOFLOW_API_KEY is missing from eco-engine/.env")


def download(name: str, slug: str, version: int, key: str) -> None:
    target = OUT / name
    if (target / "data.yaml").exists():
        print(f"{name}: already downloaded")
        return
    with urllib.request.urlopen(f"https://api.roboflow.com/{slug}/{version}/yolov8?api_key={key}", timeout=120) as r:
        link = json.load(r)["export"]["link"]
    with urllib.request.urlopen(link, timeout=600) as r:
        data = r.read()
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for member in z.infolist():  # refuse paths that escape the target folder
            dest = (target / member.filename).resolve()
            if not str(dest).startswith(str(target.resolve())):
                raise ValueError(f"unsafe path in archive: {member.filename}")
        z.extractall(target)
    print(f"{name}: {len(data) / 1e6:.1f} MB -> {target}")


if __name__ == "__main__":
    key = api_key()
    for name, (slug, version) in DATASETS.items():
        download(name, slug, version, key)

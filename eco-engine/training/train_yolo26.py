"""
Fine-tunes a YOLO26 detector on one of the downloaded datasets and saves a candidate to models/.

    python training/train_yolo26.py windows --size s --epochs 60
    python training/train_yolo26.py solar   --size s --epochs 80

Starts from Ultralytics' COCO-pretrained yolo26{size}.pt. All classes are merged into one
(single_cls), because each detector finds one thing. The best checkpoint is copied to
models/yolo26_<dataset>_candidate.pt; compare and promote it before the app uses it.
"""

import argparse
import os
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODELS = HERE.parent / "models"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", choices=["windows", "solar"])
    ap.add_argument("--size", default="s", choices=["n", "s", "m"])
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--fraction", type=float, default=1.0, help="share of training images (timing runs)")
    ap.add_argument("--batch", type=int, default=8)
    args = ap.parse_args()

    import torch
    from ultralytics import YOLO

    torch.set_num_threads(max(1, (os.cpu_count() or 2) - 1))
    os.chdir(MODELS)  # base weights are downloaded / cached next to the other models
    data = HERE / "datasets" / args.dataset / "data.yaml"
    model = YOLO(f"yolo26{args.size}.pt")
    name = f"{args.dataset}-yolo26{args.size}"
    model.train(
        data=str(data), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, device="cpu",
        single_cls=True, fraction=args.fraction, workers=2, patience=15, cos_lr=True,
        project=str(HERE / "runs"), name=name, exist_ok=True, plots=False, verbose=False,
    )
    best = HERE / "runs" / name / "weights" / "best.pt"
    if args.fraction >= 1.0 and best.exists():
        candidate = MODELS / f"yolo26_{args.dataset}_candidate.pt"
        shutil.copy2(best, candidate)
        print(f"Saved candidate {candidate}; compare it before promotion.")


if __name__ == "__main__":
    main()

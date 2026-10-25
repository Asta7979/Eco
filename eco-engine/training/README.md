# Training the YOLO26 detectors

Project Eco's photo analysis uses YOLOv8 models today. These scripts train YOLO26 replacements
on public datasets. The app switches to YOLO26 only when it beats YOLOv8 in `compare_models.py`.

```bash
# 1. Put your Roboflow key in eco-engine/.env:  ROBOFLOW_API_KEY=...
python training/download_datasets.py          # -> training/datasets/ (not committed)

# 2. Train (CPU works; a GPU is ~20x faster). Saves models/yolo26_<dataset>_candidate.pt
python training/train_yolo26.py solar   --size s --epochs 60
python training/train_yolo26.py windows --size s --epochs 40

# 3. Compare with the current YOLOv8 model
python training/compare_models.py windows --photos path/to/house/photos
python training/compare_models.py solar

# 4. After reviewing the solar results, promote that candidate and restart the app
python training/compare_models.py solar --promote
```

## Why YOLO26 is trained rather than used out of the box

YOLOE-26, the open-vocabulary YOLO26 model, was tested on five UK house photos. It found 0–4
windows where the trained YOLOv8 facade model found 5–29, and it found no rooftop solar panels.
Fine-tuning on labelled facade and aerial data is needed.

## Datasets (Roboflow Universe, CC BY 4.0)

| Detector | Dataset | Size |
|---|---|---|
| Windows | [WindowDitection](https://universe.roboflow.com/gautam-parmar-2u4ak/windowditection) by Gautam Parmar | 871 facade photos, 15,199 windows |
| Rooftop solar (aerial) | [solar plane](https://universe.roboflow.com/rooftop-jcnyk/solar-plane) by the "rooftop" workspace | 305 satellite images, 3,680 panels |

Both are licensed CC BY 4.0: keep this attribution when you publish models trained on them.

Doors, garage doors and basement openings stay on the YOLOv8 facade model for now. The public
door datasets found were either interior doors or had fewer than 20 images.

## Results (13 October 2026)

Scores on each dataset's held-out **test** images (never used in training), plus window counts
on five independent UK house photos from Wikimedia Commons, at the app's 0.25 confidence.

| Detector | Model | Test mAP50 | Test mAP50-95 | 5 UK photos | Used in app |
|---|---|---|---|---|---|
| Rooftop solar | YOLOv8 (previous) | 0.408 | 0.168 | - | |
| Rooftop solar | **YOLO26s, 60 epochs on CPU** | **0.868** | **0.533** | - | **Yes** |
| Rooftop solar | YOLO26s, Roboflow cloud training | 0.753 | - | - | |
| Windows | **YOLOv8 facade (current)** | 0.242 | 0.143 | **21, 5, 13, 7, 29** | **Yes** |
| Windows | YOLO26s, 80 epochs on Colab T4 | 0.509 | 0.325 | 18, 2, 10, 5, 14 | |
| Windows | YOLO26s, Roboflow cloud training | 0.379 | - | - | |

Solar switched to YOLO26 (`models/yolo26_solar.pt`). Windows stays on YOLOv8: YOLO26 scores higher
on its own dataset but finds fewer windows on independent UK photos, so it does not yet win on both.
The comparison script reports window results but does not promote window models because the app
does not load a YOLO26 window checkpoint.
`training/train_yolo26_colab.ipynb` trains on a free Colab GPU in about 30 minutes.

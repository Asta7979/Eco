"""
Tests for the third-party model integrations: the safe loader, the EPC potential-band
predictor and SAM request validation. Model files are optional; tests that need them skip
when they are absent so CI without weights still passes.
"""

import io
import pickle
import zipfile
from pathlib import Path

import pytest

from backend.engine import safe_models
from backend.engine.epc_predictor import BANDS, EpcPredictor, NATIVE_MODEL
from backend.engine.vision_engine import segment_image

MODELS = Path(__file__).resolve().parent.parent / "models"


class _Evil:
    def __reduce__(self):
        import os
        return (os.system, ("echo pwned",))


def _torch_like_zip(tmp_path, payload) -> Path:
    path = tmp_path / "evil.pt"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("archive/data.pkl", pickle.dumps(payload))
    return path


def test_audit_rejects_code_execution_pickles(tmp_path):
    with pytest.raises(safe_models.UnsafeModelError):
        safe_models.audit_checkpoint(_torch_like_zip(tmp_path, _Evil()))


def test_audit_finds_globals_without_executing(tmp_path):
    globs = safe_models.pickle_globals(pickle.dumps(_Evil()))
    assert any(g.endswith(".system") for g in globs)


def test_xgb_loader_refuses_unexpected_globals(tmp_path):
    bad = tmp_path / "bad.pkl"
    bad.write_bytes(pickle.dumps(_Evil()))
    with pytest.raises(safe_models.UnsafeModelError):
        safe_models.load_xgb_booster_from_pickle(bad)


@pytest.mark.parametrize("name", ["facade_windows.pt", "facade_openings.pt", "solar_panel_yolov8.pt", "sam_vit_b_01ec64.pth"])
def test_shipped_checkpoints_pass_the_audit(name):
    path = MODELS / name
    if not path.exists():
        pytest.skip(f"{name} not downloaded")
    globs = safe_models.audit_checkpoint(path)
    assert all(g.split(".")[0] in safe_models.ALLOWED_GLOBAL_ROOTS for g in globs)


PROP = {
    "total_floor_area_m2": 92.0, "annual_gas_kwh": 15000.0, "annual_electricity_kwh": 3200.0,
    "glazing_type": "Single Glazed Timber Sash", "built_year": 1890, "solar_pv_kwp": 0.0,
    "current_epc_rating": "E",
}


def test_epc_feature_mapping_is_complete_and_flags_assumptions():
    mapped = EpcPredictor.features_for(PROP, open_measures=7)
    assert set(mapped["values"]) == set(mapped["sources"])
    assert len(mapped["values"]) == 15
    assert mapped["values"]["CURRENT_ENERGY_RATING"] == BANDS.index("E")
    assert mapped["values"]["MULTI_GLAZE_PROPORTION"] == 0.0
    assert mapped["values"]["FLOOR_HEIGHT"] == 2.75


@pytest.mark.skipif(not NATIVE_MODEL.exists(), reason="EPC model not downloaded")
def test_epc_prediction_is_a_probability_distribution():
    r = EpcPredictor().predict(PROP, open_measures=7)
    assert r["available"] and r["potential_band"] in BANDS
    assert abs(sum(r["band_probabilities"].values()) - 1.0) < 1e-3
    assert "LOW_ENERGY_LIGHTING" in r["assumed_inputs"]


@pytest.mark.parametrize("boxes", [
    [], [[0.5, 0.5, 0.4, 0.6]], [[0, 0, 1.5, 1]], [[0, 0, 1, 1]] * 25,
    [None], [1], ["abcd"], [{}], [[0, 0, None, 1]], [[0, 0, "1", 1]],
    [[False, 0, 1, 1]], [[0, 0, float("nan"), 1]], [[0, 0, float("inf"), 1]],
])
def test_segment_rejects_bad_boxes(boxes):
    with pytest.raises(ValueError):
        segment_image(b"x", boxes)

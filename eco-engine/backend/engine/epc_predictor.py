"""
EPC potential-rating predictor (external ML model).

Model: the XGBoost classifier from b-rasagna/EPC-Energy-Rating-Prediction, trained on the
EPC register to predict a home's *potential* energy rating (A-G) from 15 certificate
fields (reported accuracy 0.77, macro F1 0.66). Loaded from XGBoost's native JSON
(models/epc/epc_xgb.json), converted once from the repo's pickle without unpickling it.

Our properties do not carry every register field, so each input is either taken from
the property record, derived deterministically (carbon / cost from our own engines'
factors), or set to a stated typical value. The response lists which inputs were
assumed, and the prediction is shown as a second opinion next to our own score, never
as an official EPC.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from backend.engine.carbon_engine import UK_EF_ELEC, UK_EF_GAS, UK_UNIT_PRICE_ELEC_GBP, UK_UNIT_PRICE_GAS_GBP

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models" / "epc"
NATIVE_MODEL = MODEL_DIR / "epc_xgb.json"
PICKLED_MODEL = MODEL_DIR / "xgb_model.pkl"
FEATURES_FILE = MODEL_DIR / "feature_names.pkl"

BANDS = ["A", "B", "C", "D", "E", "F", "G"]  # the repo's ordinal encoding: A=0 ... G=6
FEATURES = [
    "CURRENT_ENERGY_RATING", "CO2_EMISS_CURR_PER_FLOOR_AREA", "TOTAL_FLOOR_AREA", "MULTI_GLAZE_PROPORTION",
    "EXTENSION_COUNT", "NUMBER_HABITABLE_ROOMS", "LOW_ENERGY_LIGHTING", "NUMBER_OPEN_FIREPLACES",
    "FLOOR_HEIGHT", "PHOTO_SUPPLY", "FIXED_LIGHTING_OUTLETS_COUNT", "REPORT_TYPE", "N_RECOMMENDATIONS",
    "TOTAL_COST_CURRENT", "COST_PER_AREA",
]
MODEL_INFO = {
    "name": "EPC potential-rating classifier (XGBoost)",
    "source": "github.com/b-rasagna/EPC-Energy-Rating-Prediction",
    "trained_on": "England & Wales EPC register certificates",
    "reported_accuracy": 0.7718,
    "reported_macro_f1": 0.6579,
}


class EpcPredictor:
    def __init__(self):
        self._booster = None
        self._lock = threading.Lock()
        self.error: Optional[str] = None

    def load(self):
        if self._booster is not None or self.error:
            return self._booster
        from backend.engine.safe_models import MODEL_LOAD_LOCK

        with MODEL_LOAD_LOCK:
            if self._booster is not None or self.error:
                return self._booster
            try:
                import xgboost as xgb

                if not NATIVE_MODEL.exists() and PICKLED_MODEL.exists():
                    from backend.engine.safe_models import load_xgb_booster_from_pickle

                    load_xgb_booster_from_pickle(PICKLED_MODEL).save_model(str(NATIVE_MODEL))
                if not NATIVE_MODEL.exists():
                    raise FileNotFoundError(f"{NATIVE_MODEL.name} not found")
                booster = xgb.Booster()
                booster.load_model(str(NATIVE_MODEL))
                if booster.num_features() != len(FEATURES):
                    raise ValueError(f"model expects {booster.num_features()} features, mapping has {len(FEATURES)}")
                self._booster = booster
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"[:300]
                logger.warning("EPC predictor unavailable: %s", self.error)
        return self._booster

    def status(self) -> Dict:
        return {**MODEL_INFO, "loaded": self._booster is not None, "error": self.error}

    @staticmethod
    def features_for(prop: Dict, open_measures: int) -> Dict:
        """Maps one of our property records onto the register fields the model was trained on."""
        area = float(prop["total_floor_area_m2"])
        gas, elec = float(prop["annual_gas_kwh"]), float(prop["annual_electricity_kwh"])
        co2_kg = gas * UK_EF_GAS + elec * UK_EF_ELEC
        cost = gas * UK_UNIT_PRICE_GAS_GBP + elec * UK_UNIT_PRICE_ELEC_GBP
        glazing = str(prop.get("glazing_type", "")).lower()
        built = int(prop.get("built_year") or 1970)
        pv_kwp = float(prop.get("solar_pv_kwp") or 0.0)
        roof_m2 = max(area / 2.0, 20.0)  # two-storey footprint approximation

        values = {
            "CURRENT_ENERGY_RATING": BANDS.index(str(prop["current_epc_rating"]).upper()[:1]),
            "CO2_EMISS_CURR_PER_FLOOR_AREA": round(co2_kg / area, 2),
            "TOTAL_FLOOR_AREA": area,
            "MULTI_GLAZE_PROPORTION": 0.0 if "single" in glazing else 100.0,
            "EXTENSION_COUNT": 0.0,
            "NUMBER_HABITABLE_ROOMS": float(min(10, max(1, round(area / 20)))),
            "LOW_ENERGY_LIGHTING": 75.0,
            "NUMBER_OPEN_FIREPLACES": 0.0,
            "FLOOR_HEIGHT": 2.75 if built < 1919 else 2.45,
            "PHOTO_SUPPLY": round(min(100.0, 100.0 * pv_kwp * 6.0 / roof_m2), 1),
            "FIXED_LIGHTING_OUTLETS_COUNT": float(max(4, round(area / 8))),
            "REPORT_TYPE": 100.0,  # RdSAP survey of an existing dwelling
            "N_RECOMMENDATIONS": float(open_measures),
            "TOTAL_COST_CURRENT": round(cost, 2),
            "COST_PER_AREA": round(cost / area, 2),
        }
        sources = {
            "CURRENT_ENERGY_RATING": "property record",
            "CO2_EMISS_CURR_PER_FLOOR_AREA": "metered gas + electricity × DESNZ factors",
            "TOTAL_FLOOR_AREA": "property record",
            "MULTI_GLAZE_PROPORTION": "glazing type",
            "EXTENSION_COUNT": "assumed (register median)",
            "NUMBER_HABITABLE_ROOMS": "estimated from floor area",
            "LOW_ENERGY_LIGHTING": "assumed (typical)",
            "NUMBER_OPEN_FIREPLACES": "assumed (register median)",
            "FLOOR_HEIGHT": "estimated from build year",
            "PHOTO_SUPPLY": "solar kWp vs roof area",
            "FIXED_LIGHTING_OUTLETS_COUNT": "estimated from floor area",
            "REPORT_TYPE": "RdSAP (existing dwelling)",
            "N_RECOMMENDATIONS": "open measures in our catalogue",
            "TOTAL_COST_CURRENT": "metered energy × unit prices (includes appliances)",
            "COST_PER_AREA": "derived",
        }
        return {"values": values, "sources": sources}

    def predict(self, prop: Dict, open_measures: int) -> Dict:
        booster = self.load()
        mapped = self.features_for(prop, open_measures)
        if booster is None:
            return {"available": False, "reason": self.error or "model not loaded", "model": MODEL_INFO}
        import xgboost as xgb

        x = np.array([[mapped["values"][f] for f in FEATURES]], dtype=np.float32)
        probs = booster.predict(xgb.DMatrix(x, feature_names=booster.feature_names))[0].tolist()
        idx = int(np.argmax(probs))
        current = mapped["values"]["CURRENT_ENERGY_RATING"]
        assumed = [f for f, s in mapped["sources"].items() if s.startswith(("assumed", "estimated"))]
        return {
            "available": True,
            "current_band": BANDS[int(current)],
            "potential_band": BANDS[idx],
            "confidence": round(float(probs[idx]), 3),
            "band_probabilities": {b: round(float(p), 4) for b, p in zip(BANDS, probs)},
            "bands_gained": int(current) - idx,
            "inputs": mapped["values"],
            "input_sources": mapped["sources"],
            "assumed_inputs": assumed,
            "model": MODEL_INFO,
        }


epc_predictor = EpcPredictor()


def predict_for_properties(props: List[Dict], open_counts: List[int]) -> List[Dict]:
    return [epc_predictor.predict(p, n) for p, n in zip(props, open_counts)]

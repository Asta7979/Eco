"""
Assessment services shared by the REST routes and the guarded advisor.

Ownership: every home belongs to the user who added it (properties.owner_uid). All reads
and evaluations go through get_property_or_404(..., owner_uid), which returns 404 for a
home that does not exist *or* belongs to someone else, so IDs cannot be probed.

PortfolioCache: the property list and FR2 ranking run the engines for every home, but
only change when that user's rows change. They are memoised per user and per data
version (row count + latest created/updated timestamp for that owner).
"""

import json
import threading
import uuid
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.db import database
from backend.engine.assessment import (
    baseline_score_request,
    detect_installed_actions,
    improvement_potential,
    run_full_assessment,
    score_drivers,
)
from backend.engine.score_engine import calculate_green_home_score
from dataclasses import asdict

from backend.schemas import FullRetrofitRequest, FullRetrofitResponse, PhysicsSummary, PropertyResponse

# SAP 2012 rating bands (score -> letter) and SAP 10 primary energy factors.
_SAP_BANDS = [(92, "A"), (81, "B"), (69, "C"), (55, "D"), (39, "E"), (21, "F"), (1, "G")]
PRIMARY_FACTOR_GAS = 1.130
PRIMARY_FACTOR_ELEC = 1.501


def epc_band_for_score(score: int) -> str:
    return next(letter for floor, letter in _SAP_BANDS if score >= floor)


def roof_status_for_depth(depth_mm: int) -> str:
    return "Good" if depth_mm >= 250 else "Partial" if depth_mm >= 100 else "Uninsulated"


def primary_energy_kwh_m2(gas_kwh: float, elec_kwh: float, area_m2: float) -> float:
    return round((gas_kwh * PRIMARY_FACTOR_GAS + elec_kwh * PRIMARY_FACTOR_ELEC) / area_m2, 1)


def derive_fields(values: Dict, explicit: set) -> Dict:
    """Fills derived fields unless the user supplied them explicitly."""
    out = dict(values)
    if "current_epc_rating" not in explicit or not out.get("current_epc_rating"):
        out["current_epc_rating"] = epc_band_for_score(int(out["current_epc_score"]))
    out["current_epc_rating"] = out["current_epc_rating"].upper()
    if "roof_insulation_status" not in explicit or not out.get("roof_insulation_status"):
        out["roof_insulation_status"] = roof_status_for_depth(int(out["roof_insulation_depth_mm"]))
    if "current_primary_energy_kwh_m2" not in explicit or out.get("current_primary_energy_kwh_m2") is None:
        out["current_primary_energy_kwh_m2"] = primary_energy_kwh_m2(
            out["annual_gas_kwh"], out["annual_electricity_kwh"], out["total_floor_area_m2"]
        )
    out["postcode"] = out["postcode"].strip().upper()
    out["address"] = out["address"].strip()
    return out


def new_property_id() -> str:
    return f"PROP-{uuid.uuid4().hex[:8].upper()}"


def get_property_or_404(property_id: str, db: Session, owner_uid: str):
    P = database.PropertyModel
    prop = db.query(P).filter(P.property_id == property_id, P.owner_uid == owner_uid).first()
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Property '{property_id}' not found.")
    return prop


def property_dict(prop) -> dict:
    return PropertyResponse.model_validate(prop).model_dump()


def evaluate_full_retrofit_core(req: FullRetrofitRequest, db: Optional[Session], owner_uid: str) -> FullRetrofitResponse:
    """Shared deterministic evaluator used by the REST endpoint, scenarios and the advisor."""
    close_db = db is None
    if close_db:
        db = database.SessionLocal()
    try:
        prop = get_property_or_404(req.property_id, db, owner_uid)
        prop_d = property_dict(prop)
        result = run_full_assessment(prop_d, req.selected_interventions)
        dag_res, base, proj = result["dag"], result["base_score"], result["proj_score"]
        carbon_res, uplift_res = result["carbon"], result["uplift"]

        total_cost = dag_res.total_estimated_cost_gbp
        payback = (
            round(total_cost / carbon_res.net_bill_savings_gbp, 1)
            if carbon_res.net_bill_savings_gbp > 0
            else None
        )
        return FullRetrofitResponse(
            property_id=prop.property_id,
            address=prop.address,
            baseline_score=base.headline_score,
            projected_score=proj.headline_score,
            score_delta=proj.headline_score - base.headline_score,
            band_transition=f"{base.band} -> {proj.band}",
            flagged_for_intervention=base.flagged_for_intervention,
            dag_plan=dag_res,
            carbon_impact=carbon_res,
            property_uplift=uplift_res,
            total_retrofit_cost_gbp=total_cost,
            net_payback_years=payback,
            already_installed=detect_installed_actions(prop_d),
            baseline_band=base.band,
            projected_band=proj.band,
            baseline_components=base.components,
            projected_components=proj.components,
            baseline_drivers=score_drivers(base),
            physics=PhysicsSummary(**asdict(result["physics"])),
        )
    finally:
        if close_db:
            db.close()


def save_evaluation(db: Session, res: FullRetrofitResponse, user_id: str) -> None:
    """Persists an audited evaluation so every quoted figure can be traced later."""
    db.add(
        database.RetrofitEvaluationModel(
            evaluation_id=uuid.uuid4().hex,
            property_id=res.property_id,
            user_id=user_id,
            baseline_score=res.baseline_score,
            projected_score=res.projected_score,
            score_delta=res.score_delta,
            flagged_for_intervention=res.flagged_for_intervention,
            delta_gas_kwh=res.carbon_impact.delta_gas_kwh,
            delta_electricity_kwh=res.carbon_impact.delta_electricity_kwh,
            annual_co2e_saved_kg=res.carbon_impact.net_co2e_reduction_kg,
            annual_cost_savings_gbp=res.carbon_impact.net_bill_savings_gbp,
            heat_pump_paradox_flag=res.carbon_impact.heat_pump_paradox_detected,
            predicted_uplift_gbp_min=res.property_uplift.predicted_uplift_gbp_min,
            predicted_uplift_gbp_max=res.property_uplift.predicted_uplift_gbp_max,
            predicted_uplift_pct_min=res.property_uplift.predicted_uplift_pct_min,
            predicted_uplift_pct_max=res.property_uplift.predicted_uplift_pct_max,
            topological_roadmap_json=res.dag_plan.model_dump_json(),
            interventions_json=json.dumps(res.dag_plan.full_resolved_action_codes),
        )
    )
    db.commit()


def delete_property_cascade(db: Session, prop) -> None:
    """Removes a home and everything derived from it (evaluations, scenarios, advisor logs)."""
    pid = prop.property_id
    db.query(database.RetrofitEvaluationModel).filter_by(property_id=pid).delete(synchronize_session=False)
    db.query(database.SavedScenarioModel).filter_by(property_id=pid).delete(synchronize_session=False)
    db.query(database.AuditLogModel).filter_by(property_id=pid).delete(synchronize_session=False)
    db.delete(prop)
    db.commit()


def _summary_row(p) -> dict:
    score = calculate_green_home_score(baseline_score_request(property_dict(p)))
    return dict(
        property_id=p.property_id,
        address=p.address,
        postcode=p.postcode,
        region=p.region,
        property_type=p.property_type,
        current_epc_rating=p.current_epc_rating,
        current_epc_score=p.current_epc_score,
        baseline_value_gbp=p.baseline_value_gbp,
        green_home_score=score.headline_score,
        band=score.band,
        flagged_for_intervention=score.flagged_for_intervention,
    )


def _ranking_row(p) -> dict:
    pot = improvement_potential(property_dict(p))
    base = pot["assessment"]["base_score"]
    return dict(
        property_id=p.property_id,
        address=p.address,
        region=p.region,
        property_type=p.property_type,
        current_epc_rating=p.current_epc_rating,
        green_home_score=base.headline_score,
        band=base.band,
        flagged_for_intervention=base.flagged_for_intervention,
        drivers=score_drivers(base),
        baseline_energy_kwh=pot["baseline_energy_kwh"],
        after_energy_kwh=pot["after_energy_kwh"],
        improvement_potential_pct=pot["improvement_potential_pct"],
        delta_gas_kwh=pot["delta_gas_kwh"],
        delta_electricity_kwh=pot["delta_electricity_kwh"],
        projected_score=pot["assessment"]["proj_score"].headline_score,
    )


class PortfolioCache:
    """Per-user, per-data-version memo of the property summaries and the FR2 ranking."""

    MAX_USERS = 256

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: "OrderedDict[str, Tuple[Tuple, List[dict], List[dict]]]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _fingerprint(db: Session, owner_uid: str) -> Tuple:
        P = database.PropertyModel
        count, created, updated = (
            db.query(func.count(P.property_id), func.max(P.created_at), func.max(P.updated_at))
            .filter(P.owner_uid == owner_uid)
            .one()
        )
        return (id(database.engine), count, str(created), str(updated))

    def _get(self, db: Session, owner_uid: str) -> Tuple[List[dict], List[dict]]:
        version = self._fingerprint(db, owner_uid)
        with self._lock:
            entry = self._entries.get(owner_uid)
            if entry and entry[0] == version:
                self._entries.move_to_end(owner_uid)
                self.hits += 1
                return entry[1], entry[2]
        props = db.query(database.PropertyModel).filter(database.PropertyModel.owner_uid == owner_uid).all()
        summaries = [_summary_row(p) for p in props]
        ranking = [_ranking_row(p) for p in props]
        ranking.sort(key=lambda r: (r["improvement_potential_pct"] is None, -(r["improvement_potential_pct"] or 0)))
        with self._lock:
            self._entries[owner_uid] = (version, summaries, ranking)
            self._entries.move_to_end(owner_uid)
            while len(self._entries) > self.MAX_USERS:
                self._entries.popitem(last=False)
            self.misses += 1
        return summaries, ranking

    def summaries(self, db: Session, owner_uid: str, region: Optional[str] = None, epc_band: Optional[str] = None) -> List[dict]:
        rows, _ = self._get(db, owner_uid)
        return [
            s for s in rows
            if (not region or s["region"] == region) and (not epc_band or s["current_epc_rating"] == epc_band)
        ]

    def ranking(self, db: Session, owner_uid: str, flagged_only: bool = False) -> List[dict]:
        _, rows = self._get(db, owner_uid)
        rows = [r for r in rows if not flagged_only or r["flagged_for_intervention"]]
        return [{**r, "rank": i + 1} for i, r in enumerate(rows)]

    def stats(self) -> Dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "users_cached": len(self._entries)}


portfolio_cache = PortfolioCache()

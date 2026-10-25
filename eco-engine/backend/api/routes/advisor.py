import uuid
from typing import List

from fastapi import APIRouter, Query

from backend.api.deps import CurrentUser, SessionDep
from backend.db import database
from backend.engine.llm_guardrails import GuardedScenarioOrchestrator
from backend.schemas import AuditLogEntry, GuardedScenarioChatRequest, GuardedScenarioChatResponse
from backend.services.assessment_service import evaluate_full_retrofit_core, get_property_or_404

router = APIRouter(tags=["AI Advisory"])

@router.post("/scenario/chat", response_model=GuardedScenarioChatResponse)
def guarded_scenario_chat(req: GuardedScenarioChatRequest, user: CurrentUser, db: SessionDep):
    """
    Guarded natural-language what-if: extracts validated intent, performs zero math in the
    LLM, reruns the deterministic engines and returns an audited explanation. Every exchange
    is written to the audit log.
    """
    # The evaluator only accepts this user's homes, including any ID named in the question.
    get_property_or_404(req.property_id, db, user.id)
    orchestrator = GuardedScenarioOrchestrator(
        full_retrofit_evaluator=lambda r: evaluate_full_retrofit_core(r, db, owner_uid=user.id)
    )
    res = orchestrator.execute_and_explain(req)
    status_label = "passed" if res.guardrail_validation_passed and res.numeric_grounding_passed else "fallback"
    db.add(
        database.AuditLogModel(
            log_id=uuid.uuid4().hex,
            property_id=res.property_id,
            user_id=user.id,
            query_text=req.user_query,
            extracted_parameters_json=res.extracted_intent.model_dump_json(),
            deterministic_result_json=res.audited_deterministic_metrics.model_dump_json(),
            guardrail_status=f"{status_label}:{res.explanation_source}",
        )
    )
    db.commit()
    return res


@router.get("/audit/logs", response_model=List[AuditLogEntry])
def list_audit_logs(user: CurrentUser, db: SessionDep, limit: int = Query(20, ge=1, le=200)):
    """The signed-in user's most recent guarded-advisor exchanges, newest first."""
    A = database.AuditLogModel
    return db.query(A).filter(A.user_id == user.id).order_by(A.timestamp.desc()).limit(limit).all()

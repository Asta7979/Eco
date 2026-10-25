from fastapi import APIRouter
from sqlalchemy import text

from backend.api.deps import SessionDep
from backend.core.config import settings
from backend.services.assessment_service import portfolio_cache

router = APIRouter(tags=["System"])


@router.get("/health")
def health_check(db: SessionDep):
    """Liveness and component check."""
    db.execute(text("SELECT 1")).scalar_one()
    return {
        "status": "healthy",
        "database": "connected",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "engines_active": [
            "ScoreEngine_7Component",
            "TopologicalDAG_NetworkX",
            "DecoupledCarbon_DualFuel",
            "HedonicUplift_GBRegressor",
            "GuardedLLM_ZeroMathOrchestrator",
            "Vision_YOLOWorld_FacadeYOLOv8_SolarYOLOv8_SAM",
            "EPCPotential_XGBoost",
        ],
        "portfolio_cache": portfolio_cache.stats(),
    }

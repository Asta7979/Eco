from fastapi import APIRouter

from backend.api.routes import admin, advisor, auth, evaluate, properties, scenarios, system, v1, vision

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(properties.router)
api_router.include_router(evaluate.router)
api_router.include_router(advisor.router)
api_router.include_router(vision.router)
api_router.include_router(auth.router)
api_router.include_router(scenarios.router)
api_router.include_router(admin.router)
api_router.include_router(v1.router)

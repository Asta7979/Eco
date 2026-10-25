"""
Physics microservice: POST /simulate (fast 1R1C hourly model), POST /simulate/detailed
(EN ISO 52016-1 via pyBuildingEnergy). Internal only (service token).
"""

import asyncio
from typing import List

from fastapi import HTTPException
from pydantic import BaseModel, Field

from backend.schemas import ActionCodes, PropertyCreate
from services.common.app_factory import ServiceAuth, create_service_app
from services.physics_engine.app import hem_calculator, iso52016

app = create_service_app(
    "Eco physics engine",
    "Hourly building-physics simulations: fast 1R1C (simple-simple) and detailed ISO 52016 (pyBuildingEnergy), HEM weather.",
)


class SimulateRequest(BaseModel):
    property: PropertyCreate = Field(..., description="The home's fabric and energy data (validated, bounded)")
    measures: ActionCodes = Field(default_factory=list)


def _prop(req: SimulateRequest) -> dict:
    # The physics models read the raw fabric / energy fields; no database or ML stack needed here.
    return req.property.model_dump()


@app.post("/simulate", dependencies=[ServiceAuth])
def simulate(req: SimulateRequest):
    return hem_calculator.simulate_fast(_prop(req), list(req.measures))


# Detailed runs are CPU-heavy: at most two at a time per container.
_detailed_slots = asyncio.Semaphore(2)


@app.post("/simulate/detailed", dependencies=[ServiceAuth])
async def simulate_detailed(req: SimulateRequest):
    prop = _prop(req)
    async with _detailed_slots:
        try:
            return await asyncio.to_thread(iso52016.simulate_detailed, prop, list(req.measures))
        except ImportError:
            raise HTTPException(503, "Detailed ISO 52016 engine is not installed in this container.")


@app.get("/status", dependencies=[ServiceAuth])
def status():
    try:
        import importlib.metadata as md

        detailed = md.version("pybuildingenergy")
    except Exception:
        detailed = None
    return {"fast_model": "simple-simple 1R1C + HEM weather", "detailed_model": "pyBuildingEnergy ISO 52016", "pybuildingenergy": detailed}

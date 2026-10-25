"""
Unit tests for NetworkX Topological Retrofit Dependency DAG Engine.
Verifies prerequisite hierarchy, missing ancestor injection, and acyclic sort ordering.
"""

import pytest
import networkx as nx
from backend.engine.dag_engine import (
    solve_retrofit_dag,
    build_retrofit_dag,
    RETROFIT_ACTIONS_CATALOGUE,
)
from backend.schemas import DAGSolveRequest


def test_dag_is_directed_acyclic_graph():
    """Verify that the dependency graph has zero cyclic deadlocks."""
    dag = build_retrofit_dag()
    assert nx.is_directed_acyclic_graph(dag) is True
    assert dag.number_of_nodes() == len(RETROFIT_ACTIONS_CATALOGUE)


def test_heat_pump_prerequisite_injection():
    """
    Selecting an active heating upgrade (heat_pump) must automatically inject
    missing upstream fabric ancestors (insulation and draught proofing).
    """
    req = DAGSolveRequest(
        property_id="PROP-GB-001",
        requested_interventions=["heat_pump"],
        current_installed_actions=[],
    )
    res = solve_retrofit_dag(req)

    # Injected prerequisites must contain draught proofing and insulation
    assert "draught_proofing" in res.injected_prerequisites
    assert "wall_insulation" in res.injected_prerequisites or "loft_insulation" in res.injected_prerequisites
    assert res.fabric_first_enforced is True

    # In the resolved action sequence, prerequisites must precede heat pump
    heat_pump_idx = res.full_resolved_action_codes.index("heat_pump")
    draught_idx = res.full_resolved_action_codes.index("draught_proofing")
    assert draught_idx < heat_pump_idx


def test_already_installed_prerequisites_not_duplicated():
    """
    If home already has loft insulation and wall insulation, they should NOT
    be re-injected as pending installations.
    """
    req = DAGSolveRequest(
        property_id="PROP-GB-006",
        requested_interventions=["heat_pump"],
        current_installed_actions=["wall_insulation", "loft_insulation", "draught_proofing"],
    )
    res = solve_retrofit_dag(req)

    # Neither wall nor loft should be in injected prerequisites
    assert "wall_insulation" not in res.injected_prerequisites
    assert "loft_insulation" not in res.injected_prerequisites
    assert "draught_proofing" not in res.injected_prerequisites
    assert "heat_pump" in res.full_resolved_action_codes


def test_topological_sort_phase_ordering():
    """
    Comprehensive multi-measure retrofit must organize stages strictly by
    Phase 1 (Fabric) -> Phase 2 (Airtightness/Controls) -> Phase 3 (Heat Pump) -> Phase 4 (Solar/Battery).
    """
    req = DAGSolveRequest(
        property_id="PROP-GB-002",
        requested_interventions=["battery_storage", "heat_pump", "solar_pv"],
        current_installed_actions=[],
    )
    res = solve_retrofit_dag(req)

    stages = res.ordered_plan
    assert len(stages) >= 3

    # Check stage sequence numbers are strictly ascending
    stage_numbers = [s.stage_number for s in stages]
    assert stage_numbers == sorted(stage_numbers)

    # Solar PV must precede battery storage
    solar_idx = res.full_resolved_action_codes.index("solar_pv")
    battery_idx = res.full_resolved_action_codes.index("battery_storage")
    assert solar_idx < battery_idx

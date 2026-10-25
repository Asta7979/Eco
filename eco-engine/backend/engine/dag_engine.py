"""
Topological Retrofit Dependency DAG Solver (Project Eco).
Enforces physical prerequisite hierarchy and "fabric-first" sequence using NetworkX.

Physical Hierarchy:
    Fabric Insulation (Loft/Wall/Floor)
           │
           ▼
    Draught Proofing & Air Tightness
           │
           ▼
    Heat Pump Sizing & Commissioning
           │
           ▼
    Solar PV + Battery Storage & Flexibility

Solves:
    1. Automatic injection of missing prerequisites using nx.ancestors().
    2. Deterministic topological sorting via nx.topological_sort().
    3. Grouping into sequenced enterprise phases for project execution.
"""

from typing import Dict, List, Set, Tuple
import networkx as nx
from backend.schemas import (
    RetrofitAction,
    TopologicalStage,
    DAGSolveRequest,
    DAGSolveResponse,
)

RETROFIT_ACTIONS_CATALOGUE: Dict[str, RetrofitAction] = {
    "loft_insulation": RetrofitAction(
        code="loft_insulation",
        name="Loft Insulation Upgrade (>= 270mm)",
        category="Fabric",
        estimated_cost_gbp=1100.0,
        estimated_time_days=2,
        description="Mineral wool top-up to 300mm to eliminate up to 25% roof heat loss.",
    ),
    "wall_insulation": RetrofitAction(
        code="wall_insulation",
        name="External / Cavity Wall Insulation",
        category="Fabric",
        estimated_cost_gbp=5800.0,
        estimated_time_days=7,
        description="High-performance insulation reducing wall U-value below 0.30 W/m²K.",
    ),
    "floor_insulation": RetrofitAction(
        code="floor_insulation",
        name="Suspended Timber Underfloor Insulation",
        category="Fabric",
        estimated_cost_gbp=1400.0,
        estimated_time_days=3,
        description="Breathable membrane and insulation batts to stop underfloor draughts.",
    ),
    "window_upgrade": RetrofitAction(
        code="window_upgrade",
        name="High-Performance Double/Triple Glazing",
        category="Fabric",
        estimated_cost_gbp=4500.0,
        estimated_time_days=4,
        description="Acoustic Low-E argon-filled glazing reducing fenestration conduction loss.",
    ),
    "draught_proofing": RetrofitAction(
        code="draught_proofing",
        name="Airtightness & Comprehensive Draught Proofing",
        category="Fabric & Airflow",
        estimated_cost_gbp=650.0,
        estimated_time_days=2,
        description="Sealing floor-wall junctions, chimney flues, and perimeter apertures.",
    ),
    "smart_controls": RetrofitAction(
        code="smart_controls",
        name="Smart Thermostats & Smart TRVs",
        category="Controls",
        estimated_cost_gbp=450.0,
        estimated_time_days=1,
        description="Digital multi-zone modulation and weather-compensating temperature control.",
    ),
    "heat_pump": RetrofitAction(
        code="heat_pump",
        name="Air Source Heat Pump (ASHP) System",
        category="Heating",
        estimated_cost_gbp=9500.0,
        estimated_time_days=5,
        description="Low-temperature hydronic heat pump delivering seasonal COP of 3.2–3.6.",
    ),
    "solar_pv": RetrofitAction(
        code="solar_pv",
        name="4.0 kWp Rooftop Solar PV Array",
        category="Renewables",
        estimated_cost_gbp=5400.0,
        estimated_time_days=2,
        description="Monocrystalline solar panels generating ~3,600 kWh clean electricity annually.",
    ),
    "battery_storage": RetrofitAction(
        code="battery_storage",
        name="8.0 kWh Home Battery Storage (BESS)",
        category="Flexibility",
        estimated_cost_gbp=4200.0,
        estimated_time_days=1,
        description="Lithium-iron-phosphate battery capturing surplus solar and cheap off-peak grid power.",
    ),
    "ev_charger": RetrofitAction(
        code="ev_charger",
        name="7.4 kW Smart EV Charger",
        category="Flexibility",
        estimated_cost_gbp=850.0,
        estimated_time_days=1,
        description="Dynamic load balancing home vehicle charger synchronized with solar export.",
    ),
}


def build_retrofit_dag() -> nx.DiGraph:
    """
    Constructs the directed acyclic graph defining physical dependencies.
    Edge (u, v) means action 'u' is a strict prerequisite for action 'v'.
    """
    dag = nx.DiGraph()

    # Add all nodes
    for code, action in RETROFIT_ACTIONS_CATALOGUE.items():
        dag.add_node(code, **action.model_dump())

    # Dependency Rules:
    # 1. Wall and loft insulation must precede draught proofing & heat pump sizing
    dag.add_edge("wall_insulation", "draught_proofing")
    dag.add_edge("loft_insulation", "draught_proofing")
    dag.add_edge("window_upgrade", "draught_proofing")

    # 2. Draught proofing and controls must precede heat pump installation
    # (Prevents heat loss over-sizing, high return temperatures, and cycling inefficiencies)
    dag.add_edge("draught_proofing", "heat_pump")
    dag.add_edge("smart_controls", "heat_pump")

    # 3. Renewables and flexibility
    dag.add_edge("solar_pv", "battery_storage")
    dag.add_edge("solar_pv", "ev_charger")

    return dag


RETROFIT_DAG = build_retrofit_dag()


def solve_retrofit_dag(req: DAGSolveRequest) -> DAGSolveResponse:
    """
    Resolves requested retrofit interventions:
    1. Validates requested action codes against catalogue.
    2. Identifies and injects missing prerequisite ancestors via nx.ancestors().
    3. Excludes actions already installed in the home.
    4. Computes topological sort order.
    5. Organizes the actions into phased project milestones.
    """
    dag = RETROFIT_DAG
    requested = set(req.requested_interventions)
    already_installed = set(req.current_installed_actions)

    # Validate codes
    valid_requested = [code for code in requested if code in RETROFIT_ACTIONS_CATALOGUE]

    # Find all prerequisite ancestors for each requested action
    all_required: Set[str] = set()
    injected_prerequisites: Set[str] = set()

    for code in valid_requested:
        all_required.add(code)
        # Query networkx ancestors (all upstream dependencies)
        ancestors = nx.ancestors(dag, code)
        for anc in ancestors:
            if anc not in already_installed:
                all_required.add(anc)
                if anc not in valid_requested:
                    injected_prerequisites.add(anc)

    # Filter out actions already installed
    needed_actions = all_required - already_installed

    # Subgraph for requested actions
    subgraph = dag.subgraph(needed_actions)

    # Deterministic topological sort
    sorted_codes = list(nx.topological_sort(subgraph))

    # Phase grouping
    phases: Dict[int, List[str]] = {1: [], 2: [], 3: [], 4: []}
    for code in sorted_codes:
        category = RETROFIT_ACTIONS_CATALOGUE[code].category
        if category == "Fabric":
            phases[1].append(code)
        elif category in ("Fabric & Airflow", "Controls"):
            phases[2].append(code)
        elif category == "Heating":
            phases[3].append(code)
        else:  # Renewables & Flexibility
            phases[4].append(code)

    stage_definitions = [
        (1, "Phase 1: Fabric First (Envelope Thermal Upgrade)", "Reduces space heating demand and prevents thermal bridges before heating system overhaul."),
        (2, "Phase 2: Air Tightness & Smart Control", "Eliminates parasitic draughts and establishes weather-compensated temperature regulation."),
        (3, "Phase 3: Heating Decarbonisation (Clean Heat)", "Installs optimally sized heat pump operating at peak low-temperature seasonal COP."),
        (4, "Phase 4: On-site Generation & Flexibility", "Maximises self-consumption of zero-carbon solar power and off-peak grid arbitrage."),
    ]

    ordered_stages: List[TopologicalStage] = []
    total_cost = 0.0

    for stage_num, title, rationale in stage_definitions:
        codes_in_phase = phases.get(stage_num, [])
        if codes_in_phase:
            action_objects = [RETROFIT_ACTIONS_CATALOGUE[c] for c in codes_in_phase]
            total_cost += sum(a.estimated_cost_gbp for a in action_objects)
            ordered_stages.append(
                TopologicalStage(
                    stage_number=stage_num,
                    stage_title=title,
                    actions=action_objects,
                    stage_rationale=rationale,
                )
            )

    return DAGSolveResponse(
        requested_actions=valid_requested,
        injected_prerequisites=sorted(list(injected_prerequisites)),
        full_resolved_action_codes=sorted_codes,
        ordered_plan=ordered_stages,
        fabric_first_enforced=len(injected_prerequisites) > 0 or any(c in needed_actions for c in ["wall_insulation", "loft_insulation", "draught_proofing"]),
        total_estimated_cost_gbp=round(total_cost, 2),
    )

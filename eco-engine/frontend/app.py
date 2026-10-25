"""
Project Eco (EcoRetrofit AI Engine) - Interactive Streamlit Dashboard.
Designed for Lloyds Banking Group (Megathon X Problem Statement 2).

Architecture:
    - 100% Deterministic Python Calculation Engines for all mathematical metrics.
    - Guarded LLM layer for natural language scenario interpretation with zero math hallucinations.
    - Full dual-fuel decoupled accounting, NetworkX DAG topological roadmap, and hedonic property valuation.
"""

import sys
from pathlib import Path
import json
import streamlit as st

# Ensure backend can be imported directly when running locally or via API
CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.engine.assessment import run_full_assessment
from backend.engine.dag_engine import RETROFIT_ACTIONS_CATALOGUE
from backend.engine.llm_guardrails import GuardedScenarioOrchestrator

# Page Configuration
st.set_page_config(
    page_title="Project Eco | Retrofit Intelligence",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Product design system lives beside the app so presentation can evolve without
# touching the deterministic calculation layer.
st.markdown(f"<style>{(CURRENT_DIR / 'styles.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

# Load Properties Dataset
@st.cache_data
def load_properties():
    json_path = REPO_ROOT / "data" / "properties.json"
    if json_path.exists():
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

properties = load_properties()
if not properties:
    st.error("No properties data found. Please run data/generate_synthetic_epc.py first.")
    st.stop()

# All numbers come from the shared deterministic pipeline used by the REST API,
# so the dashboard and the web app always agree.
def run_deterministic_retrofit(prop, selected_interventions):
    return run_full_assessment(prop, selected_interventions)


from frontend.ui import render_dashboard

render_dashboard(
    properties,
    run_deterministic_retrofit,
    RETROFIT_ACTIONS_CATALOGUE,
    GuardedScenarioOrchestrator,
)

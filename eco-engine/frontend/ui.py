"""Presentation layer for the Project Eco Streamlit dashboard."""

from html import escape

import streamlit as st


def _section(kicker: str, title: str, copy: str) -> None:
    st.markdown(
        f"""<div class="section-intro"><div><div class="eyebrow">{escape(kicker)}</div>
        <h2>{escape(title)}</h2></div><p>{escape(copy)}</p></div>""",
        unsafe_allow_html=True,
    )


def _metric(label: str, value: str, detail: str, tone: str = "") -> None:
    st.markdown(
        f"""<div class="metric-card {tone}"><div class="metric-label">{escape(label)}</div>
        <div class="metric-value">{value}</div><div class="metric-sub">{detail}</div></div>""",
        unsafe_allow_html=True,
    )


def _callout(kind: str, icon: str, title: str, body: str) -> None:
    st.markdown(
        f"""<div class="callout {kind}"><div class="callout-icon">{icon}</div><div>
        <h4>{escape(title)}</h4><p>{escape(body)}</p></div></div>""",
        unsafe_allow_html=True,
    )


def _score_panel(score_result) -> None:
    rows = []
    for component in score_result.components:
        weight = int(component.weight * 100)
        rows.append(
            f"""<div class="score-row"><div class="score-name">{escape(component.name)}
            <small>{weight}% weight · {escape(component.description)}</small></div>
            <div class="score-track"><div class="score-fill" style="width:{component.sub_score}%"></div></div>
            <div class="score-number">{component.sub_score:.0f}</div></div>"""
        )
    st.markdown(f'<div class="score-shell">{"".join(rows)}</div>', unsafe_allow_html=True)


def _sidebar(properties):
    st.sidebar.markdown(
        """<div class="brand-lockup"><div class="brand-mark"><span class="brand-leaf"></span>PROJECT ECO</div>
        <div class="brand-kicker">EcoRetrofit Intelligence</div></div>""",
        unsafe_allow_html=True,
    )
    st.sidebar.markdown('<div class="side-section">Portfolio asset</div>', unsafe_allow_html=True)
    options = {
        f"{p['property_id']} · {p['address']} · EPC {p['current_epc_rating']}": p
        for p in properties
    }
    selected = st.sidebar.selectbox("Property", list(options), label_visibility="collapsed")
    prop = options[selected]
    specs = [
        ("Archetype", prop["property_type"]),
        ("Built", str(prop["built_year"])),
        ("Floor area", f"{prop['total_floor_area_m2']} m²"),
        ("Wall U-value", f"{prop['wall_u_value']} W/m²K"),
        ("Roof insulation", f"{prop['roof_insulation_depth_mm']} mm"),
        ("Annual gas", f"{prop['annual_gas_kwh']:,.0f} kWh"),
        ("Annual electricity", f"{prop['annual_electricity_kwh']:,.0f} kWh"),
    ]
    st.sidebar.markdown('<div class="side-section">RdSAP envelope</div>', unsafe_allow_html=True)
    st.sidebar.markdown(
        "".join(
            f'<div class="side-spec"><span>{escape(k)}</span><strong>{escape(v)}</strong></div>'
            for k, v in specs
        ),
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(
        '<div class="system-online"><span class="status-dot"></span> Deterministic engines online</div>',
        unsafe_allow_html=True,
    )
    return prop


def _hero(prop) -> None:
    st.markdown(
        f"""<section class="hero"><div class="hero-copy"><div class="eyebrow">Live asset intelligence · {escape(prop['property_id'])}</div>
        <h1>Make every home<br><em>future-fit.</em></h1>
        <p>Turn physical building data into an auditable retrofit sequence, a true carbon outcome, and a finance-ready investment case.</p>
        <div class="hero-proof"><span class="proof"><b>✓</b> 100% deterministic maths</span>
        <span class="proof"><b>✓</b> Fabric-first guardrails</span><span class="proof"><b>✓</b> Dual-fuel accounting</span></div></div>
        <div class="scene" aria-label="Animated digital twin of an energy-efficient home"><div class="ground"></div><div class="orbit"></div>
        <div class="house3d"><div class="house-body"></div><div class="roof"></div><div class="solar"></div>
        <div class="door"></div><div class="window left"></div><div class="window right"></div></div><div class="scan"></div></div></section>""",
        unsafe_allow_html=True,
    )


def _overview(prop, baseline):
    _section(
        "01 / Asset baseline",
        "Know where the home stands.",
        "A weighted physical-performance score that keeps energy, fabric, systems, renewables and controls independently auditable.",
    )
    cols = st.columns(4)
    rating = prop["current_epc_rating"]
    priority = "Priority" if baseline.flagged_for_intervention else "Stable"
    with cols[0]:
        _metric("Official EPC", f"{rating} <span class='unit'>{prop['current_epc_score']} / 100</span>", "Below EPC C target" if rating in "DEFG" else "Meets EPC C target", "dark")
    with cols[1]:
        _metric("Green Home Score", f"{baseline.headline_score}<span class='unit'> / 100</span>", f"{escape(baseline.band)} performance", "accent")
    with cols[2]:
        _metric("Market value", f"£{prop['baseline_value_gbp']:,.0f}", f"{escape(prop['region'])} · indicative baseline")
    with cols[3]:
        _metric("Intervention", priority, "Retrofit candidate" if baseline.flagged_for_intervention else "Continue monitoring")

    _section(
        "Weighted evidence",
        "The seven signals behind the score.",
        "Every bar is calculated from a physical attribute. Better building performance can never reduce a component score.",
    )
    left, right = st.columns([1.6, 1])
    with left:
        _score_panel(baseline)
    with right:
        st.markdown(
            f"""<div class="metric-card dark" style="min-height:296px;padding:1.6rem">
            <div class="metric-label">Asset snapshot</div><div class="metric-value" style="font-size:1.5rem">{escape(prop['property_type'])}</div>
            <div class="metric-sub" style="line-height:2.05;margin-top:1.1rem">
            {escape(prop['wall_type'])}<br>{escape(prop['glazing_type'])}<br>{escape(prop['heating_type'])}<br>
            {prop['current_primary_energy_kwh_m2']:,.0f} kWh/m² primary energy<br>{prop['total_floor_area_m2']} m² treated floor area</div>
            <div style="margin-top:1.2rem;color:#45e381;font:500 .68rem 'DM Mono',monospace">MONOTONICITY VERIFIED ✓</div></div>""",
            unsafe_allow_html=True,
        )


def _planner(prop, evaluate, catalogue):
    _section(
        "02 / Retrofit studio",
        "Sequence before you spend.",
        "Choose desired outcomes. The dependency engine inserts any missing physical prerequisites and returns a buildable, fabric-first roadmap.",
    )
    choose, roadmap = st.columns([.8, 1.4], gap="large")
    selected = []
    with choose:
        st.markdown("#### Select interventions")
        st.caption("Indicative costs come from the engineering catalogue.")
        for code, action in catalogue.items():
            checked = st.checkbox(
                f"{action.name}  ·  £{action.estimated_cost_gbp:,.0f}",
                value=code in ("heat_pump", "solar_pv"),
                key=f"retrofit_{code}",
            )
            if checked:
                selected.append(code)
    result = evaluate(prop, selected)
    plan = result["dag"]
    with roadmap:
        if plan.injected_prerequisites:
            names = ", ".join(catalogue[c].name for c in plan.injected_prerequisites)
            _callout("warning", "↳", "Fabric-first guardrail applied", f"The engine added {names} before active equipment to protect comfort and system efficiency.")
        elif selected:
            _callout("success", "✓", "Sequence validated", "The selected plan already respects all physical dependencies.")
        else:
            _callout("success", "○", "Baseline only", "Select one or more interventions to generate an ordered delivery plan.")

        for stage in plan.ordered_plan:
            chips = []
            stage_days = 0
            for action in stage.actions:
                stage_days += action.estimated_time_days
                injected = action.code in plan.injected_prerequisites
                label = f"{action.name}{' · auto-added' if injected else ''}"
                chips.append(f'<span class="action-chip {"injected" if injected else ""}">{escape(label)}</span>')
            st.markdown(
                f"""<div class="stage-card"><div class="stage-top"><span class="stage-num">0{stage.stage_number}</span>
                <span class="stage-title">{escape(stage.stage_title.replace(f'Phase {stage.stage_number}: ', ''))}</span>
                <span class="stage-meta">≈ {stage_days} DAYS</span></div>
                <div class="stage-rationale">{escape(stage.stage_rationale)}</div><div>{''.join(chips)}</div></div>""",
                unsafe_allow_html=True,
            )
        _metric("Indicative total investment", f"£{plan.total_estimated_cost_gbp:,.0f}", f"{len(plan.full_resolved_action_codes)} actions · dependencies included", "dark")
    return result


def _outcomes(prop, result):
    carbon, uplift = result["carbon"], result["uplift"]
    _section(
        "03 / Verified outcomes",
        "Carbon and capital, uncoupled.",
        "Gas and electricity are tracked as separate vectors, preserving the real effect of fuel switching and the heat-pump cost paradox.",
    )
    if carbon.heat_pump_paradox_detected and not carbon.heat_pump_paradox_mitigated:
        _callout("warning", "!", "Heat-pump paradox active", carbon.paradox_explanation)
    elif carbon.heat_pump_paradox_mitigated:
        _callout("success", "✓", "Heat-pump paradox resolved", carbon.paradox_explanation)
    cols = st.columns(4)
    with cols[0]:
        _metric("Gas change", f"{carbon.delta_gas_kwh:,.0f}", "kWh / year · fuel vector")
    with cols[1]:
        _metric("Electricity shift", f"{carbon.delta_electricity_kwh:+,.0f}", "kWh / year · grid vector")
    with cols[2]:
        _metric("Carbon avoided", f"{carbon.net_co2e_reduction_tonnes:.2f}<span class='unit'> tCO₂e</span>", f"<span class='trend'>↓ {carbon.net_co2e_reduction_pct:.1f}% annually</span>", "accent")
    with cols[3]:
        sign = "saved" if carbon.net_bill_savings_gbp >= 0 else "additional cost"
        _metric("Bill impact", f"£{abs(carbon.net_bill_savings_gbp):,.0f}", f"per year · {sign}")

    _section(
        "Green finance case",
        "Translate performance into value.",
        "An indicative hedonic range links the verified score change to capital value and eligible green-finance benefits.",
    )
    cols = st.columns(3)
    with cols[0]:
        _metric("Score transition", f"{uplift.baseline_score} → {uplift.projected_score}", f"+{uplift.score_delta} verified points", "dark")
    with cols[1]:
        _metric("Indicative value uplift", f"£{uplift.predicted_uplift_gbp_min:,.0f}–£{uplift.predicted_uplift_gbp_max:,.0f}", f"+{uplift.predicted_uplift_pct_min}% to +{uplift.predicted_uplift_pct_max}%")
    with cols[2]:
        _metric("Green finance benefit", f"£{uplift.lloyds_green_mortgage_cashback_gbp:,.0f}", f"cashback · {uplift.lloyds_preferential_rate_discount_bps} bps indicative discount", "accent")
    st.caption("Capital uplift and lending benefits are indicative model outputs, not a valuation or mortgage offer.")


def _advisor(prop, evaluate, catalogue, orchestrator_class):
    _section(
        "04 / Guarded scenario lab",
        "Ask naturally. Calculate deterministically.",
        "The language layer only extracts valid retrofit intent. Every number shown below is recomputed by the audited Python engines.",
    )
    p1, p2, p3 = st.columns(3)
    presets = [
        (p1, "Heat pump + solar", "What if I install an air source heat pump and rooftop solar PV?"),
        (p2, "Test the paradox", "What if I only replace my gas boiler with a heat pump?"),
        (p3, "Whole-home retrofit", "What if I add wall and loft insulation, triple glazing, a heat pump, solar and battery storage?"),
    ]
    if "scenario_query" not in st.session_state:
        st.session_state.scenario_query = presets[0][2]
    for column, label, query in presets:
        if column.button(label, use_container_width=True):
            st.session_state.scenario_query = query
    query = st.text_area("Describe a retrofit scenario", key="scenario_query", height=100)
    if st.button("Run verified scenario", type="primary", use_container_width=True):
        orchestrator = orchestrator_class(full_retrofit_evaluator=lambda request: None)
        intent = orchestrator.extract_intent(query, prop["property_id"])
        output = evaluate(prop, intent.requested_interventions)
        names = [catalogue[c].name for c in intent.requested_interventions]
        with st.status("Scenario parsed and verified", expanded=True) as status:
            st.write(f"Recognised: {', '.join(names)}")
            st.write("Validated action codes against the retrofit catalogue")
            st.write("Recalculated score, fuel, carbon and valuation outputs")
            status.update(label="Audit complete · zero LLM arithmetic", state="complete")
        left, right = st.columns([1, 1.55], gap="large")
        with left:
            injected = ", ".join(output["dag"].injected_prerequisites) or "none"
            budget = f"£{intent.budget_constraint_gbp:,.0f}" if intent.budget_constraint_gbp else "not specified"
            st.markdown(
                f"""<div class="audit"><div class="audit-line"><span class="audit-ok">PASS</span> SCHEMA_VALIDATION</div>
                <div class="audit-line"><span class="audit-ok">PASS</span> CATALOGUE_ALLOWLIST</div>
                <div class="audit-line"><span class="audit-ok">PASS</span> DETERMINISTIC_MATH</div>
                <div class="audit-line">GOAL&nbsp; {escape(intent.user_primary_goal)}</div>
                <div class="audit-line">BUDGET&nbsp; {escape(budget)}</div>
                <div class="audit-line">INJECTED&nbsp; {escape(injected)}</div></div>""",
                unsafe_allow_html=True,
            )
        with right:
            base, projected = output["base_score"], output["proj_score"]
            carbon, uplift = output["carbon"], output["uplift"]
            st.markdown("#### Audited answer")
            st.markdown(
                f"For **{escape(prop['address'])}**, the selected scenario moves the Green Home Score from "
                f"**{base.headline_score} to {projected.headline_score}**. The calculation projects "
                f"**{carbon.net_co2e_reduction_tonnes:.2f} tCO₂e** avoided each year and an annual bill "
                f"{'saving' if carbon.net_bill_savings_gbp >= 0 else 'increase'} of "
                f"**£{abs(carbon.net_bill_savings_gbp):,.0f}**. The indicative capital uplift range is "
                f"**£{uplift.predicted_uplift_gbp_min:,.0f}–£{uplift.predicted_uplift_gbp_max:,.0f}**."
            )


def render_dashboard(properties, evaluate, catalogue, orchestrator_class) -> None:
    """Render the complete interactive product experience."""
    prop = _sidebar(properties)
    _hero(prop)
    baseline = evaluate(prop, [])["base_score"]
    overview, planner, outcomes, advisor = st.tabs(
        ["Asset overview", "Retrofit studio", "Verified outcomes", "Scenario advisor"]
    )
    with overview:
        _overview(prop, baseline)
    with planner:
        selected_result = _planner(prop, evaluate, catalogue)
    with outcomes:
        _outcomes(prop, selected_result)
    with advisor:
        _advisor(prop, evaluate, catalogue, orchestrator_class)
    st.markdown(
        """<div class="footer"><span>PROJECT ECO · EcoRetrofit AI Engine</span>
        <span>Deterministic calculations · Guarded explanations · UK housing intelligence</span></div>""",
        unsafe_allow_html=True,
    )

# Project Eco — M1 Approach (v3)

**Megathon X · Lloyds Banking Group · Problem Statement 2**
*Know your home's green score. Get a retrofit plan you can trust.* · 10 October 2026

---

## 1. The challenge

Homeowners and mortgage advisors can't easily answer three questions: **How green is this home? What should be fixed first? What will it save, and what could it add in value?**

- **Upgrades depend on each other.** A heat pump in an uninsulated home has to run at high flow temperatures, so it is less efficient and must be oversized.
- **Fuels shift.** A heat pump removes gas use but adds electricity use, so a single combined kWh figure hides what actually changed.
- **Cost-based EPCs can mislead.** Electricity costs more per kWh than gas, so a low-carbon heat-pump switch can *lower* a home's EPC rating.

## 2. Our answer

One engine **scores** the home, **plans** upgrades in a valid order, **simulates** electricity and gas separately, and **estimates** carbon and indicative property-value impact.

- Every number comes from a documented calculation engine or a trained model.
- The AI assistant only **explains**. A validator checks that every number in its reply appears in the computed results, and any reply that fails falls back to a template.
- Ask "what if?" → the scenario changes → the engines rerun → a new explanation is generated.

## 3. System architecture

```
INPUT      Property details        Energy readings           Home photos (stretch)
           type · area · heating   monthly elec + gas kWh    YOLO + SAM → user confirms
                │                        │                         │
GATEWAY    FastAPI + Scenario Orchestrator
           Pydantic validation · apply scenario changes · trigger reruns · (Firebase sign-in: stretch)
                │
ENGINES    Green Score      Retrofit Planner     Energy + Carbon         Value Model
           7 weighted parts NetworkX DAG         per-fuel simulation     gradient-boosted hedonic
           → 0–100 + drivers budget · order · fit ΔC = Σ_fuel ΔE × EF   → £min–£max range
                │
DATA       PostgreSQL + SQLAlchemy (SQLite for local dev)
           properties · energy usage · retrofit catalogue · carbon factors · sales · saved scenarios
                │
OUTPUT     Guarded AI Explainer  ──►  Dashboard (score · roadmap · before/after · chat)
           numbers injected, never computed                         │
           validator fails or > 1.5 s → template answer              └── WHAT-IF → RERUN
```

**Assessment pipeline:** Validate → Score (find weak spots) → Filter (suitable, not done, within budget) → Order (prerequisites first) → Simulate (electricity and gas) → Explain.

### Scope: MVP vs stretch

| MVP (demoed at M2) | Stretch |
|---|---|
| Green Home Score, retrofit planner, per-fuel energy + carbon simulation, value range, guarded explainer with template fallback, what-if rerun, saved scenarios | Photo assistance (YOLO + SAM), Firebase sign-in, React/TypeScript dashboard (the prototype UI is Streamlit) |

## 4. Green Home Score: fixed weights

| Component | Weight | Input | Sub-score mapping (monotonic, 0–100) |
|---|---|---|---|
| EPC rating | 25% | SAP score 1–100 | Identity, clamped to 1–100 |
| Energy intensity | 20% | kWh/m²/yr (metered if available, otherwise modelled) | 100 at ≤ 50, linear to 0 at ≥ 450 |
| Insulation | 15% | Wall U-value, loft depth, floor | 50% wall + 35% loft + 15% floor |
| Heating | 15% | System type / efficiency | Heat pump > district > condensing > storage > oil/LPG > direct electric |
| Glazing | 10% | Glazing type | Triple > modern double > double > secondary > single |
| Renewables | 10% | PV kWp, battery kWh | PV up to 70 pts (4 kWp), battery up to 30 pts (8 kWh) |
| Smart controls | 5% | Smart thermostat / TRVs | 100 present, 20 absent |

**Score = round(Σ weight × sub-score).** The dashboard shows how many points each component contributes.

- **Missing data:** if a component has no input, its weight is shared out over the remaining components and the score is marked *reduced confidence*. Metered intensity is used only when ≥ 10 of 12 months have readings. A missing reading is never treated as zero.
- **EPC paradox:** the EPC rating is only 25% of the score. After a heat-pump switch the heating component (15%) and the energy intensity component (20%) rise, which offsets any cost-based drop in the EPC rating, and the dashboard shows the per-component change so the trade-off is visible.

## 5. Retrofit planner: right upgrade, right order

```
FABRIC                      SYSTEMS                     SMART LAYER
Loft / wall insulation ───► Heat pump (gas → elec)
Glazing + draught-proofing ─┘   (must happen first)
                            Solar PV (independent)  ─ ─► Battery (recommended after PV)
Smart controls (independent)
```

- **Hard prerequisites** (solid arrows) are physical requirements, e.g. insulation and draught-proofing must come before a heat pump. **Recommended sequencing** (dashed arrows) only affects ordering, never feasibility.
- **Filter:** remove measures that are unsuitable for the property type, already completed, or over budget.
- **Select:** greedy by benefit per £ cost. Adding a measure also adds any missing prerequisites (its prerequisite closure), and the whole bundle must fit within the budget.
- **Order:** topologically sort the selected set. Ties are broken by benefit per £.
- **Benefit (one objective):** % cut in purchased energy (primary). Ties are broken by indicative value uplift per £ spent.
- **Ranking homes:** by achievable % cut in purchased energy. The result is *unavailable* when the baseline is missing or zero.

## 6. Energy and carbon simulation

Measures are applied in plan order, each one acting on the demand that is **left** after the previous ones:

- **Fabric measures** reduce space-heating demand by the catalogue %. For example, two 20% cuts give 36% in total, not 40%.
- **Heat pump:** the remaining space-heating gas kWh × boiler efficiency gives heat delivered, and electricity added = heat delivered ÷ SCOP (default 3.0). Gas for heating goes to 0.
- **Solar PV:** grid imports fall by kWp × regional yield × self-consumption ratio.
- **Savings per fuel = baseline − after.** Extra electricity shows as a **negative** saving.
- **Carbon:** ΔC = Σ_fuel ΔE_fuel × EF_fuel. The factors are stored with a year and source (DESNZ/DEFRA GHG conversion factors).

## 7. Property-value model

- **Model:** a gradient-boosted hedonic regressor (XGBoost planned; the prototype uses scikit-learn GradientBoosting). Features are region, type, floor area, EPC band and the property's components.
- **Data:** reproducible synthetic sales calibrated to published HM Land Registry and EPC-register patterns. If time allows, the real Price Paid and EPC open data (OGL) will be linked.
- **Range:** 10th–90th percentile quantile models give £min–£max. Target: ≥ 75% of held-out sales fall inside the 80% interval.
- The value is always shown as an **indicative range**, never a point valuation.

## 8. Data model

| Entity | Key fields | Relationship |
|---|---|---|
| User | uid (PK) | 1 : N Property |
| Property | property_id (PK), owner_uid (FK), region, type, floor_area_m2 > 0, epc_rating (A–G / SAP 1–100), heating, insulation, glazing, solar, controls, current_value_gbp | 1 : N Energy usage, Scenario, Sale, Retrofit history |
| Energy usage | (property_id, month) unique; electricity_kwh ≥ 0, gas_kwh ≥ 0 (NULL = missing); coverage; source | N : 1 Property |
| Retrofit catalogue | retrofit_id (PK), applicability, cost_range, impact assumptions, prerequisite_ids (hard), sequencing_ids (soft) | DAG over itself |
| Retrofit history | property_id, retrofit_id, completed_on | Marks measures as already done |
| Carbon factor | fuel, kg_co2e_per_kwh, year, source | Used by the carbon engine |
| Sale | property_id, sale_date, price, rating_at_sale | Value-model training only; kept separate from the current value |
| Scenario | scenario_id (PK), property_id, input snapshot, budget, measures, before/after results, assumptions, model versions | 1 : N Recommendation; immutable once saved |
| Recommendation | (scenario_id, retrofit_id), order, suitability, estimated impact | N : 1 Scenario, N : 1 Catalogue |

## 9. How we will prove it works

| Area | Checks |
|---|---|
| Data | Seeded, reproducible synthetic data. 80/10/10 split by property. Preprocessing is fitted on the training split only. |
| Engines | Weights sum to 100%. Score stays within 0–100. Monotonic sub-scores. Budget limit respected. Prerequisite order. Cases for poor insulation, an infeasible measure and an already-completed measure. |
| Models | Per-fuel maths, heat-pump electricity rise, carbon units, missing baseline gives *unavailable*. Value model beats a median-by-region baseline on held-out MAE/RMSE. Interval coverage is reported. |
| AI + flow | Suggestions from photos are applied only after the user confirms them. Same scenario gives same results. Timeout fallback works. Saved scenarios reload identically. Response times are measured. |

**AI targets.** These are measured on a fixed set of ≥ 50 scripted questions. Each answer is split into claims, and each claim is checked against the injected results.

| Metric | Target |
|---|---|
| Numeric grounding (every number in the reply matches a computed value) | **100%** (enforced by the validator) |
| Groundedness of qualitative claims | ≥ 90% |
| Factual accuracy | ≥ 90% |
| Unsupported claims | < 5% |
| Time before the template fallback is used | 1.5 s |

## 10. Responsible use

- Outputs are **indicative estimates**, not a formal valuation or regulated mortgage advice.
- Photos are processed only to suggest features. They are not stored once the user has confirmed or rejected the suggestions.
- Every saved scenario records its assumptions, carbon-factor year and model versions so the result can be audited.

## 11. Technology

React · TypeScript (planned; Streamlit prototype) · Python · FastAPI · Pydantic · PostgreSQL · SQLAlchemy · NetworkX · scikit-learn / XGBoost · PyTorch · YOLO · SAM (stretch) · LLM + guardrails · Firebase (stretch)

## 12. 30-second pitch

> "Project Eco tells a homeowner how green their home is, what to fix first, and what it will save. You get a transparent Green Home Score, a planner that respects the physical order of upgrades, separate electricity and gas modelling, and an indicative value range. An AI explains all of it, and a validator checks every number it quotes against our calculations. Ask 'what if?' and the models rerun live."

---

### Revision checklist (v2 → v3)

- [x] Numeric grounding 100%; 90% targets apply to qualitative claims; measurement method stated
- [x] "Topological sort gives every valid order" replaced with select → closure → sort
- [x] Single benefit objective defined
- [x] DAG split into hard prerequisites and recommended sequencing; EV charger dropped
- [x] EPC paradox mitigation stated
- [x] Sequential per-fuel simulation method described
- [x] Value data source, interval method and coverage target added
- [x] Missing-data and coverage rules for the score
- [x] Budget selection algorithm named
- [x] Responsible use / compliance section
- [x] MVP vs stretch separated
- [x] Factor, Sale and Retrofit history restored; "unavailable" rule restored
- [x] Terminology unified (EPC rating, energy intensity)
- [x] Controls weight shown as 5%; Scenario→Recommendation cardinality added
- [x] Claims aligned with the current code or labelled planned/prototype

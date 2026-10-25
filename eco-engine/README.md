# Project Eco (EcoRetrofit AI Engine)
### Enterprise Green Finance & Building Performance Platform
**Designed for Lloyds Banking Group** | *Megathon X Problem Statement 2*

[![Python](https://img.shields.io/badge/Python-3.12%20%7C%203.13-006A4E?style=flat&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-v0.110+-00553E?style=flat&logo=fastapi)](https://fastapi.tiangolo.com)
[![Streamlit](https://img.shields.io/badge/Streamlit-v1.32+-FF4B4B?style=flat&logo=streamlit)](https://streamlit.io)
[![Pydantic v2](https://img.shields.io/badge/Pydantic-v2.6+-E92063?style=flat&logo=pydantic)](https://docs.pydantic.dev)
[![Tests](https://img.shields.io/badge/Pytest-128%20Passed-brightgreen?style=flat&logo=pytest)](https://pytest.org)

---

## 1. Executive Summary & Mandate

The UK Net Zero 2050 housing roadmap requires upgrading **28 million homes**. To support Lloyds Banking Group’s target of financing sustainable homes across its £450bn+ mortgage book, **Project Eco (EcoRetrofit AI Engine)** delivers an auditable, deterministic green finance calculation engine coupled with a guarded AI advisory layer.

### Key Real-World Technical Gaps Solved:
1. **The EPC Heat Pump Paradox:**
   Standard UK Energy Performance Certificates (EPCs) evaluate homes based on operational running costs (£/m²/year) rather than true carbon intensity. Because grid electricity costs 3–4x more per kWh than natural gas (Ofgem benchmark ~£0.245/kWh vs ~£0.062/kWh), installing a zero-carbon heat pump into an uninsulated home can paradoxically downgrade the property's headline EPC rating! Eco evaluates physical carbon efficiency independently of raw fuel price ratios and enforces fabric upgrades to optimize heat pump seasonal COP.
2. **Uncoupled Dual-Fuel Accounting:**
   Generic calculators aggregate gas and electricity into a single blended kWh total, obscuring grid dynamics. Eco separates gas elimination ($\Delta\text{kWh}_{\text{gas}} < 0$) from electricity import shifts ($\Delta\text{kWh}_{\text{elec}} \lessgtr 0$).
3. **Sequence Ignorance (Fabric-First):**
   Installing a heat pump in a draughty, uninsulated home leads to thermal failure, high flow temperatures (COP drops to ~2.4), and grid spikes. Eco enforces a strict physical prerequisite hierarchy via a NetworkX Directed Acyclic Graph (DAG).

---

## 2. Core Architectural Rule: Strict Partitioning

Eco enforces an absolute separation of responsibilities:
```
┌─────────────────────────────────────────────────────────────┐
│               Guarded LLM Layer (Advisory)                  │
│   • Performs ZERO math. Calculates NO numbers.              │
│   • Extracts Pydantic v2 JSON intent parameters.            │
│   • Explains audited deterministic results to homeowners.    │
└──────────────────────────────┬──────────────────────────────┘
                               │ Dispatches Validated Parameters
                               ▼
┌─────────────────────────────────────────────────────────────┐
│          Deterministic Calculation Engines (Python)         │
│   • Computes 100% of all numerical outputs.                 │
│   • 7-Component Green Home Score (0-100)                    │
│   • NetworkX Topological Dependency DAG                     │
│   • Decoupled Dual-Fuel Carbon Accounting                   │
│   • Hedonic Property Valuation Regressor & Lloyds Cashback  │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Mathematical & Algorithmic Engines

### A. 7-Component Green Home Score (`backend/engine/score_engine.py`)
Headline score equation:
$$S = \text{round}\left( \sum_{k=1}^{7} w_k \cdot s_k \right)$$

| Component $k$ | Weight $w_k$ | Physical Basis | Monotonicity Constraint |
|---|---|---|---|
| Energy Efficiency | **25%** | Standard SAP / RdSAP score (1–100) | $\partial s_1 / \partial \text{SAP} \ge 0$ |
| Energy Intensity | **20%** | Primary energy use in $\text{kWh/m}^2/\text{yr}$ | Decreasing kWh increases $s_2$ |
| Insulation Quality | **15%** | Wall U-value, roof depth (mm), floor insulation | Lower U-value increases $s_3$ |
| Heating System | **15%** | Low-carbon efficiency (Heat pump vs Gas vs Resistive) | Heat pump > Condensing > Non-cond |
| Windows & Glazing | **10%** | Fenestration (Triple > Modern Double > Single) | Higher spec increases $s_5$ |
| Renewable Energy | **10%** | Solar PV capacity (kWp) & Battery Storage (kWh) | More capacity increases $s_6$ |
| Smart Controls | **5%** | Multi-zone smart thermostats & TRVs | Presence increases $s_7$ |

**Bands:** Highly Efficient (80–100), Good (65–79), Moderate (50–64), Poor (30–49, Flagged), Very Poor (0–29, Flagged).

### B. Topological Retrofit DAG Solver (`backend/engine/dag_engine.py`)
Enforces the physical prerequisite hierarchy using `networkx.DiGraph`:
$$\text{Fabric Insulation} \longrightarrow \text{Draught Proofing} \longrightarrow \text{Heat Pump Sizing} \longrightarrow \text{Solar PV + Battery}$$
- Uses `nx.ancestors` to automatically detect and inject missing insulation prerequisites if a user attempts to install a heat pump in an uninsulated home.
- Uses `nx.topological_sort` to structure a 4-phase execution roadmap.

### C. Decoupled Dual-Fuel Carbon Engine (`backend/engine/carbon_engine.py`)
Uses official UK DESNZ/DEFRA greenhouse gas conversion factors:
- Natural Gas: $\text{EF}_{\text{gas}} = 0.18316\text{ kg CO}_2\text{e/kWh}$
- Grid Electricity: $\text{EF}_{\text{elec}} = 0.20705\text{ kg CO}_2\text{e/kWh}$

$$\Delta\text{CO}_2\text{e} = (\Delta\text{kWh}_{\text{gas}} \times 0.18316) + (\Delta\text{kWh}_{\text{elec}} \times 0.20705)$$
$$\text{Net CO}_2\text{e Saved} = -\Delta\text{CO}_2\text{e}$$

### D. Hedonic Property Value Regressor (`backend/engine/uplift_engine.py`)
Trained on UK Land Registry price-paid datasets and EPC band transitions to predict:
- Capital uplift range (+Min% to +Max% in £ GBP)
- Lloyds Green Living Reward cashback (£500, £1,000, or £2,000)
- Preferential green mortgage rate discount (10–20 bps).

---

## 4. Repository Structure

```
eco-engine/
├── README.md                          # Project documentation & math specs
├── requirements.txt                   # Production dependencies
├── docker-compose.yml                 # Multi-container orchestration
├── Dockerfile.backend                 # FastAPI service container
├── Dockerfile.frontend                # Streamlit UI container
├── schema.sql                         # Relational schema (PostgreSQL & SQLite)
├── pytest.ini                         # Test runner configuration
├── data/
│   ├── generate_synthetic_epc.py      # Builds the synthetic test fixture
│   └── properties.json                # Test fixture only (never loaded into the app database)
├── backend/                           # layout follows fastapi/full-stack-fastapi-template
│   ├── main.py                        # App factory: lifespan + DB migration, GZip, CORS, cached static portal
│   ├── core/config.py                 # Typed settings (pydantic-settings, ECO_* env vars / .env)
│   ├── api/
│   │   ├── deps.py                    # SessionDep / CurrentUser dependencies
│   │   ├── main.py                    # api_router collecting every route module
│   │   └── routes/                    # properties, evaluate, advisor, vision, auth, scenarios, system
│   ├── services/assessment_service.py # Shared evaluator + PortfolioCache (per-data-version memo)
│   ├── auth.py                        # Local bcrypt/JWT accounts or Supabase Auth
│   ├── schemas.py                     # Strict Pydantic v2 schemas
│   ├── db/database.py                 # SQLAlchemy 2.0 ORM & connections
│   └── engine/                        # score, dag, carbon, uplift, assessment, llm_*, vision_engine
├── frontend/
│   ├── app.py, ui.py, styles.css      # Interactive Streamlit dashboard
│   └── web/                           # Web portal served by FastAPI at /portal
│       ├── index.html, landing.js     # Landing page with live engine preview
│       ├── app.html, app.js           # Assessment app: score, planner, portfolio, advisor
│       └── common.js, style.css       # Shared design system, icons, theme
└── tests/
    ├── test_score_engine.py           # Monotonicity & weight validation
    ├── test_dag_engine.py             # DAG cycle freedom & prerequisite injection
    ├── test_carbon_engine.py          # Dual-fuel math & paradox resolution
    └── test_assessment_api.py         # FR2 ranking, numeric grounding, intent parsing, REST API
```

---

## 5. Quickstart & Execution Instructions

### A. Local Setup (FastAPI + Streamlit)
```bash
# 1. Clone repository & change directory
cd eco-engine

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) regenerate the synthetic test fixture used by the tests
python data/generate_synthetic_epc.py

# 4. Run test suite
python -m pytest tests -p no:anyio -v

# 5. Start FastAPI Backend (Port 8000)
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# 6. Start Streamlit Interactive UI (Port 8501)
streamlit run frontend/app.py
```

- **Landing page:** http://localhost:8000/ (redirects to `/portal/`)
- **Assessment app:** http://localhost:8000/portal/app.html (deep links such as `?property=PROP-GB-004#portfolio`)

### Optional: LLM-worded explanations (FR7)
The guarded advisor works without an API key: it answers from a deterministic template.
To have an LLM reword the audited results, configure one provider:

| `ECO_LLM_PROVIDER` | Needs | Default `ECO_LLM_MODEL` |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-opus-5-5` |
| `gemini` | `GEMINI_API_KEY` | `gemini-2.5-flash` |
| `ollama` | `OLLAMA_HOST` (e.g. `http://localhost:11434`) and `ollama pull gemma4` | `gemma4` |

If `ECO_LLM_PROVIDER` is unset, the first of those credentials found is used. Every number
in the reply is checked against the engine output, and any reply that quotes an unverified
number, times out or errors falls back to the template. `ECO_LLM_TIMEOUT_S` defaults to `8`
(`60` for Ollama). In Docker, point `OLLAMA_HOST` at `http://host.docker.internal:11434`.

### Sign-in (built in, no keys needed)
Sign-in works out of the box: email + password accounts are stored in the app database
with bcrypt-hashed passwords, and the API issues signed JWTs (7-day sessions, failed
logins throttled). Pages: `/portal/login.html` (`?mode=signup` opens account creation).
Set a stable `ECO_AUTH_SECRET` of at least 32 characters in production; startup fails
without it. Local development creates `.auth_secret` when the variable is unset.
`ECO_LOCAL_AUTH=0` turns accounts off (the workspace then cannot be used).

### Security model and production checklist
Audited end to end; `tests/test_security.py` (17 tests) keeps every fix verified.

| Area | Control |
|---|---|
| Sessions | HS256 JWT, algorithm pinned, 7-day expiry; user row checked on every request (deleted / deactivated accounts lose access at once); password change revokes all older sessions |
| Passwords | bcrypt cost 12; 8+ characters, max 72 bytes (bcrypt's limit, enforced instead of crashing); constant-time-ish login (dummy hash for unknown emails) and identical error messages |
| RBAC | Every data route scoped to the owner; `/admin/*` requires a superuser (`ECO_SUPERUSER_EMAILS`, or promoted in the Admin tab); admins can't demote / delete themselves |
| Rate limits (per IP) | login 10/min, register 10/h, password change 10/min, vision 20/min, SAM 6/min, advisor 30/min, everything else 600/min; plus per-email and per-IP failed-login throttles |
| Request limits | JSON bodies 256 KB, uploads 13 MB; images must be JPEG/PNG/WebP under 40 MP (header checked before decoding, blocks decompression bombs); every list / string / number in a request body is bounded |
| Headers | CSP with `script-src 'self'` (no inline scripts anywhere), `frame-ancestors 'none'`, nosniff, Referrer-Policy, Permissions-Policy (camera self only), COOP/CORP, HSTS on HTTPS, `no-store` on personal data, no `Server` banner |
| CORS | Same-origin only by default; `ECO_CORS_ORIGINS` for explicit origins; `*` refused in production |
| Errors | Generic 500 body, details only in server logs; vision status hides internal error text |
| SQL | SQLAlchemy ORM (parameterised); the only raw SQL uses fixed identifiers |

**Before going live:** set `ENVIRONMENT=production`, a random 32+ character `ECO_AUTH_SECRET`,
`POSTGRES_PASSWORD`, and `ECO_SUPERUSER_EMAILS`; serve behind HTTPS (set
`ECO_TRUST_PROXY_HEADERS=1` only if the proxy overwrites `X-Forwarded-For`). API docs are off in
production unless `ECO_ENABLE_DOCS=1`. The Docker images run as a non-root user; the legacy
Streamlit prototype only starts with `docker compose --profile legacy up` and binds to localhost.
Known residual risk: the session token lives in `localStorage` (mitigated by the strict CSP; moving
to HttpOnly cookies would also need CSRF protection). Supabase mode loads supabase-js from jsDelivr.

### Accounts and data ownership
- **No demo data.** The database starts empty; every home is added by a signed-in user
  (`POST /properties`) and belongs to them (`properties.owner_uid`).
- **The workspace requires sign-in.** `app.html` redirects to `login.html`; every API call
  carries the session token. Homes, rankings, evaluations, the advisor, EPC predictions,
  scenarios and advisor logs are scoped to the caller. Another user's home returns 404,
  including when its ID is named inside an advisor question.
- **Add / edit / delete homes** in the app (dialog with validation). The server derives the
  EPC band from the SAP score, primary energy intensity from metered gas + electricity
  (SAP 10 factors 1.130 / 1.501) and roof status from loft depth.
- **Deleting a home** removes its evaluations, saved scenarios and advisor logs.
  **`DELETE /auth/me`** ("Delete my account and data" on the Saved page) removes everything.
- **Database:** SQLite runs with foreign keys, WAL and a busy timeout; Postgres uses
  `pool_pre_ping`. New columns are added in place at start-up, so existing databases upgrade.
- Public, data-free endpoints: `/health`, `/catalogue`, `/auth/*`, `/vision/*`, `/evaluate/score|dag-roadmap|carbon|uplift`.

### Computer vision (photo assistance, FR6)
The **Vision AI** tab (`/portal/app.html#vision`) takes an upload, drag-and-drop, paste or a
live camera capture. Demo links: `app.html?sample=photo#vision`, `app.html?sample=thermal#vision`.

| Mode | Pipeline |
|---|---|
| Exterior | **YOLO-World v2** open-vocabulary detection (windows, doors, solar panels, chimneys, outdoor heat-pump units) at 960 px with per-class confidence floors, plus an OpenCV **PV texture detector** (HSV colour mask + cell-grid edge density) and an OpenCV geometric window detector as fallbacks |
| Thermal | Palette-aware **heat index** → Otsu threshold → morphology → connected components; each hotspot gets an outline and a zone (roof line, wall, window / door, floor line) and the frame gets a 0–100 heat-loss index |

Detections become *suggestions* (add loft / wall / floor insulation, windows, draught-proofing;
or "solar PV / heat pump looks installed"). The planner changes only after the user confirms
them. Images are processed in memory and never stored. Weights: `models/yolov8s-worldv2.pt`
(the first start also downloads CLIP text weights, about 340 MB, in the background; until then the
OpenCV detectors answer). Endpoints: `POST /vision/analyze` (multipart `file`, `mode`), `GET /vision/status`.

### Third-party models (computer vision + EPC second opinion)

| File in `models/` | Source repo | Used for | Licence |
|---|---|---|---|
| `sam_vit_b_01ec64.pth` | facebookresearch/segment-anything | Box-prompted masks (precise outlines) via `POST /vision/segment` | Apache-2.0 |
| `facade_windows.pt`, `facade_openings.pt` | elaounijassim/Detection-of-low-facade-openings-using-YOLOv8-and-SAM | Windows; doors, basement openings, garages | **No licence file**: check before commercial use |
| `solar_panel_yolov8.pt` | ArielDrabkin/Solar-Panel-Detector (final mosaic-augmentation model) | Rooftop PV in **aerial** (satellite / drone) images | **No licence file** |
| `epc/epc_xgb.json` (from `xgb_model.pkl`) | b-rasagna/EPC-Energy-Rating-Prediction | Predicted *potential* EPC band, `GET /properties/{id}/epc-prediction` | **No licence file** |
| `yolov8s-worldv2.pt` | Ultralytics | Open-vocabulary detection (chimneys, outdoor units, solar) | AGPL-3.0 (Ultralytics) |

**Safe loading** (`backend/engine/safe_models.py`). Pickle-based weights can run code when
loaded, and Ultralytics loads with `weights_only=False`. Here every file is first audited
statically (`pickletools`, nothing executed) and refused if it references anything outside
torch / ultralytics / collections. YOLO checkpoints then load through
`torch.load(weights_only=True)` with an allowlist of exactly the declared classes (old
`ultralytics.yolo.*` names are mapped to their current paths); SAM loads as plain tensors; the
XGBoost model is read out of its pickle as raw bytes and saved once in XGBoost's native JSON.
A test proves an `os.system` payload is rejected.

**Performance.** All torch work runs on one dedicated inference thread (several worker
threads each spawned their own OpenMP team and fought for the cores: SAM took 40 s instead of
7 s). Measured on CPU: detection ~0.6 s, thermal ~0.6 s, SAM ~7 s per photo (one encoder pass
for all boxes; the UI shows boxes first and swaps in outlines when SAM returns).

**Domain notes.** The solar YOLOv8 was trained on top-down satellite tiles; on street-level
photos it misfired on planters, so it runs in aerial mode only (exterior photos use YOLO-World +
a PV texture detector). The EPC model needs 15 register fields; 6 are estimated or assumed for
our homes and are listed in the response and UI. It is shown as a second opinion, not an EPC.

### Optional: Supabase sign-in instead

1. Create a project at [supabase.com](https://supabase.com).
2. Copy `.env.example` to `.env`, set `SUPABASE_URL=https://tajtlvwdgdkrxisuqyrm.supabase.co`,
   and add `SUPABASE_PUBLISHABLE_KEY` from the project's Connect dialog or API Keys page.
   Use the project root without `/rest/v1/`. A legacy `SUPABASE_ANON_KEY` also works.
3. Authentication → URL Configuration: set **Site URL** to `http://localhost:8000/portal/`
   and add `http://localhost:8000/portal/*` to **Redirect URLs**.
4. Optional: Authentication → Providers → enable **Google** for "Continue with Google".
5. To store app data in Supabase, set `DATABASE_URL` to the exact Session pooler
   PostgreSQL connection string from the project's Connect dialog. The `/rest/v1/`
   URL is not a PostgreSQL connection string. On Postgres the backend enables row-level security
   on its tables at startup, so Supabase's public REST API cannot read them.
6. Restart the API. The sign-in page is at `/portal/login.html`.

The browser signs in with supabase-js; the API verifies each request's bearer token with
Supabase Auth and stores scenarios per user (`saved_scenarios` table).

### Performance notes
Measured on the 20-home sample (local, median of 7 requests):

| What | Before | After |
|---|---|---|
| `GET /properties/ranking` | 8.6 ms (recomputed every call) | 1.8 ms (`PortfolioCache`, invalidated when property rows change) |
| Planner checkbox click | 1 database write per click | 0 writes (`persist=false` preview; saved scenarios keep the audit snapshot) |
| Landing page download | 812 KB (uncompressed, JPG) | 152 KB (gzip + WebP) |
| App shell download | 172 KB | 47 KB (gzip); assets cached for 7 days, CSS/JS for 1 hour |

The browser also keeps a small query cache (`ecoQuery`, TanStack Query style: shared
in-flight requests and a stale time), and the portfolio table keeps its filter, sort and page
in the URL (`?tq=…&tsort=…&tpage=…`, nuqs style) so views can be shared as links.

### Key endpoints
| Method | Path | Purpose |
|---|---|---|
| GET | `/properties` | Homes with Green Home Score, band and intervention flag |
| GET | `/properties/ranking` | FR2: drivers and improvement-potential ranking (`?flagged_only=true`) |
| GET | `/catalogue` | Retrofit measures with cost and duration |
| POST | `/evaluate/full-retrofit` | All four engines, per-component before/after (`?persist=false` skips saving) |
| POST | `/scenario/chat` | Guarded what-if advisor, written to the audit log |
| GET | `/audit/logs` | Recent advisor exchanges |
| GET | `/auth/config` | Which sign-in provider the browser should use |
| POST | `/auth/register`, `/auth/login` | Local accounts: returns a signed session token |
| GET | `/auth/me` | The signed-in user |
| POST | `/vision/analyze` | Computer vision on an uploaded photo (exterior or thermal) |
| POST | `/vision/live` | One live-camera frame on the fast path (trained detectors at 640 px, max 2 MB, 150 frames/min) |
| GET | `/vision/status` | Which vision models are loaded |
| POST | `/vision/segment` | SAM outlines for detection boxes (multipart `file`, JSON `boxes`) |
| GET | `/properties/{id}/epc-prediction` | Predicted potential EPC band (external XGBoost model) |
| POST / GET | `/scenarios` | Save / list the signed-in user's scenarios (bearer token) |
| GET / DELETE | `/scenarios/{id}` | Open or delete one of your scenarios |

### Versioned API (`/api/v1`)
Stable contracts for integrations; every route needs a signed-in user.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/score` | Green Home Score for one home (7 components, band) |
| POST | `/api/v1/retrofit` | Full retrofit plan: DAG order, dual-fuel carbon, value uplift, physics |
| POST | `/api/v1/cv` | Computer vision on a photo (proxied to the vision service) |
| POST | `/api/v1/scenario` | Guarded what-if advisor (LLM parses, Python calculates) |
| GET | `/api/v1/physics/{id}` | Fast hourly heat-loss model: heating need, heat-pump size, per-measure savings |
| POST | `/api/v1/physics/{id}/detailed` | EN ISO 52016-1 run via pyBuildingEnergy (physics service, Linux) |

### Building physics
- **Fast model (always on):** a 1R1C hourly model (ISO 13790 simple hourly method, after
  *simple-simple*) driven by the Home Energy Model London weather file, shifted per UK region,
  with the SAP heating schedule. It is calibrated to the home's own meter readings
  (prebound effect), so old, under-heated homes are not credited with savings they cannot
  realise. Heat pumps are sized from design heat loss at 21 °C inside and −3 °C outside.
- **Detailed model (optional):** the same house as a 4-façade EN ISO 52016-1 building in
  pyBuildingEnergy, run by the physics service. The response reports agreement with the fast
  model. On Windows hosts that block h5py it is unavailable (HTTP 503); in Docker it works.

### Database migrations (Alembic)
The schema is versioned in `migrations/`. On startup the gateway upgrades a fresh or managed
database to the latest revision, and stamps an older database that predates Alembic.
```powershell
alembic upgrade head                      # apply migrations
alembic revision --autogenerate -m "..."  # after changing backend/db models
```

### B. Docker Compose Deployment (microservices)
```
            browser
               |  :8000 (only public port)
          +---------+      X-Service-Token, private network (no internet)
          | gateway |----------------+-----------------+
          +---------+                |                 |
           |  auth, REST, Alembic    v                 v
           v               +----------------+  +----------------+  +-------------+
      PostgreSQL 16        | vision  :8001  |  | physics :8003  |  | llm   :8002 |
                           | YOLOv8, SAM,   |  | 1R1C + ISO     |  | parse and   |
                           | YOLO-World     |  | 52016          |  | explain     |
                           +----------------+  +----------------+  +------+------+
                                                                          | HTTPS
                                                                    Anthropic API
```
```powershell
cd eco-engine
Copy-Item .env.example .env
# Fill in .env: POSTGRES_PASSWORD, ECO_AUTH_SECRET (32+ chars), ECO_SERVICE_TOKEN (32+ chars)
docker compose up --build -d
```
- **gateway** (`backend/`): the only published port. Auth, ownership, the deterministic
  engines, the web portal and `/api/v1`. It calls the other services over HTTP with a
  shared `ECO_SERVICE_TOKEN`, and still checks every number in an LLM reply against the
  engine output (zero trust).
- **vision** (`services/vision_service`): `POST /detect`, `POST /segment`. It has no internet
  access, so model weights, including the CLIP text encoder for YOLO-World in
  `models/clip/`, are mounted read-only from `./models`.
- **llm** (`services/llm_service`): `POST /parse`, `POST /explain`. This is the only container
  with an LLM API key (or `OLLAMA_HOST`) and outbound internet. It never calculates; it may only choose
  catalogue measures and reword numbers it was given.
- **physics** (`services/physics_engine`): `POST /simulate`, `POST /simulate/detailed`.
- If `VISION_SERVICE_URL`, `LLM_SERVICE_URL` or `PHYSICS_SERVICE_URL` is unset (for example
  in a local `uvicorn` run), the gateway does that work in-process instead. So the
  single-process setup in section A still works without Docker.
- Each service has its own requirements file in `requirements/`, so the gateway image
  carries no PyTorch and the LLM and physics images carry neither PyTorch nor scikit-learn.
- PostgreSQL stays on the private network; its data persists in the `postgres_data` volume.
  `DATABASE_URL`, if set, takes precedence over the `POSTGRES_*` settings.
- Keep the same `ECO_AUTH_SECRET` across rebuilds, or local-account sessions are invalidated.
- Legacy Streamlit prototype: `docker compose --profile legacy up` (localhost:8501 only).
- **Health check:** http://localhost:8000/health (includes a database query). API docs are
  turned off in production.

---

## 6. Test Suite Verification

Run the comprehensive unit test suite:
```bash
python -m pytest tests -p no:anyio -v
```

128 tests (the ISO 52016 smoke test runs only on Linux, in the `physics-detailed` CI job) verify:
- Building physics: heat loss, heating need monotonic in insulation, prebound calibration,
  heat-pump sizing, ISO 52016 geometry matching the fast model.
- Microservices: service-token auth, the `/detect`, `/parse`, `/explain` and `/simulate`
  contracts, gateway routing, LLM replies re-validated by the gateway, `/api/v1` contracts.
- Security: headers and CSP, rate limits, upload limits, token handling, password changes.
- Strict sum of component weights = 1.0.
- Monotonicity across all 7 score subcomponents.
- Automatic intervention flags on Poor & Very Poor bands.
- NetworkX DAG cycle freedom and topological sequence ordering.
- Automatic prerequisite injection for uninsulated heat pump installations.
- Official UK conversion factor accuracy and dual-fuel decoupling.
- EPC Heat Pump Paradox detection and fabric-first mitigation.
- FR2 improvement-potential formula, fuel split, and "unavailable" baselines.
- Numeric grounding: AI replies that quote unverified numbers fall back to the template.
- Intent parsing (whole-word measures, £ and "k" budgets) and the REST endpoints.
- Accounts: sign-in required for data, per-user isolation of homes / evaluations / advisor / scenarios, CRUD validation, cascade deletes, account deletion.

### Known limitations
- **FR6 computer vision** uses facade YOLOv8 + YOLO-World + SAM; SAM takes ~7 s per photo on CPU. Thermal heat-loss is a relative index from the image palette, not a calibrated temperature.
- **FR5** uses scikit-learn GradientBoosting on synthetic data; XGBoost and real Land Registry data are planned.
- The carbon engine models heat-pump switching from **gas** only. Oil, LPG and electric-heated homes
  (e.g. PROP-GB-007, -008, -013, -014) currently show no heating saving from a heat pump.

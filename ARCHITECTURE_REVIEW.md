# LogiPilot AI — Architecture Review

**Scope:** whole repository (177 tracked files, ~15 kLOC application code).
**Baseline:** commit `35016ea`, branch `main`. Test suite: **178 passed** in 9.2 s.
**Purpose:** Phase 1 audit deliverable — the map that every later fix is justified against.

---

## 1. What this system is

A constraint-aware logistics decision platform for the Kerala south corridor. It has two
halves that meet only at the HTTP boundary:

| Half | Nature | Entry point |
| --- | --- | --- |
| **Deterministic routing pipeline** | Pure graph algorithms over Neo4j. Reproducible, no LLM. | `GET /api/routes/plan`, `/candidates`, `/replan` |
| **Multi-agent reasoning pipeline** | 13-node LangGraph workflow with LLM calls. | `POST /api/chat`, `/api/chat/stream` |

The design intent — stated in `app/routing/engine.py` and honoured in the code — is that
**the LLM never selects a corridor**. The graph proposes candidates; Google Routes only
enriches candidates already chosen; the LLM only explains and validates. That separation
is the most valuable property in this codebase and every recommendation below preserves it.

---

## 2. Folder structure

```
Regional Finals/
├── app/                          FastAPI backend (the "already functional" half)
│   ├── api/
│   │   ├── main.py               app factory + lifespan (warms schema, compiles graph)
│   │   └── routes/               10 routers, 38 endpoints
│   ├── core/                     config (pydantic-settings), logging, models, LangGraph state
│   ├── domain/                   business logic: network, constraints, delay, fleet, geo, ml
│   ├── routing/                  deterministic engine: cost model, strategies, overlay, replanner
│   ├── agents/                   13 agents, one per workflow node
│   ├── workflow/                 LangGraph assembly + runner (sync + SSE streaming)
│   ├── kg/                       Neo4j client, Cypher sanitiser, schema introspection
│   ├── mcp/                      tool registry, builtin tools, MCP client/server
│   ├── llm/                      chat model factory + structured-output helper
│   ├── search/                   web search (DuckDuckGo) + graph-aware search engine
│   ├── observability/            run metrics, business KPIs, LangSmith tracing
│   └── security/                 RBAC, PII, prompt-injection, toxicity, guardrails, audit
├── dashboard/                    React 19 + Vite 6 frontend
│   └── src/
│       ├── Root.jsx              hand-rolled 3-route history router + theme + session
│       ├── App.jsx               admin shell; owns nearly all dashboard state
│       ├── pages/                9 pages (7 tab-switched inside App, 2 route-level)
│       ├── components/           13 presentational + map components
│       ├── data/fleet.js         the single API client for the whole frontend
│       └── config/demoAuth.js    front-end-only role gate (explicitly not security)
├── data/                         CSVs, ML training set, JSONL stores
├── scripts/                      graph loading, dataset generation, MCP server, verification
└── tests/                        12 test modules, 178 tests
```

### Structural debris found

| Item | Status |
| --- | --- |
| `dashboard/dashboard/` (`package.json` + 5.7 kLOC lock file) | Accidental self-referencing install (`"logipilot-ai-dashboard": "file:.."`). Dead. |
| `.claude/launch.json` → `logipilot-console` config | Points at a `frontend/` directory that does not exist. Dead. |
| `dashboard/playwright-*.png` (3 files, ~3.4 kLOC of base64) | Debug screenshots committed as source. |
| `llm.py` | Deliberate back-compat shim for `app.llm.factory`. Keep — documented. |
| `test.json` | Untyped scratch payload at repo root. |
| `.env` | **Tracked in git with live credentials.** See §8. |

---

## 3. Architecture and data flow

### 3.1 Deterministic route planning — `GET /api/routes/plan`

```
HTTP request
   │
   ├─ load_network()                     ← Neo4j: Location, CONNECTED_TO,
   │     app/domain/network.py             ALTERNATE_ROUTE, Incident/HAS_INCIDENT
   │     • builds bidirectional adjacency
   │     • attach_alternates(): resolves ALTERNATE_ROUTE extra_distance against
   │       the primary Dijkstra distance, then promotes it to a real edge
   │
   ├─ resolve_all(locations)             ← 3-tier coordinates:
   │     app/domain/geo.py                 node props → static table → live geocode
   │                                       (out-of-Kerala results rejected by bbox)
   │
   ├─ RoutingEngine.plan()               ← the deterministic core
   │     app/routing/engine.py
   │     │
   │     ├─ overlay_from_incidents()     ← incident → edge penalty / node block
   │     │     app/routing/overlay.py      (origin & destination are protected)
   │     ├─ GraphProjection              ← network + overlay + coordinates, one view
   │     ├─ StrategyFactory.create()     ← astar | dijkstra | yen, by node count
   │     │     app/routing/factory.py      and whether alternatives are wanted
   │     ├─ CostModel.evaluate(leg)      ← 7-dimension dynamic edge cost:
   │     │     app/routing/cost.py          time, distance, congestion, weather,
   │     │                                  money, carbon, hub, risk, quality,
   │     │                                  priority, HOS, SLA, capacity, ML
   │     └─ _score()                     ← normalise into RouteCandidate list
   │
   ├─ network.build_path(candidate.stops)  ← re-materialise + verify every leg
   │                                         against the graph (rejects unverifiable)
   │
   ├─ predict_with_live_traffic()        ← asyncio.gather over routes
   │     app/domain/delay.py
   │     ├─ fetch_live_traffic()         ← Google Routes API v2 (TRAFFIC_AWARE)
   │     │     app/domain/live_traffic.py  duration vs staticDuration = measured delay
   │     ├─ plausibility gate            ← reject readings implying <12 or >110 km/h
   │     └─ factor model                 ← incidents + weather + peak, each with evidence
   │
   ├─ apply_profile() + evaluate_candidate()
   │     app/domain/fleet.py / constraints.py
   │     └─ ~18 hard + soft constraints, each returning a ConstraintCheck
   │
   ├─ recommend = min(feasible, key=predicted_total_minutes)
   ├─ _build_alerts()
   └─ record_decision()                  ← data/route_decisions.jsonl (KPI feed)
```

**Key invariant:** a route that the graph cannot substantiate never reaches the response.
`build_path` returns `None` on any missing leg, and those candidates are dropped.

### 3.2 Agent workflow — `POST /api/chat`

13 LangGraph nodes. Topology from `app/workflow/graph.py`:

```
START → guardrail ─┬─(blocked)→ observability
                   └→ security ─┬─(blocked)→ observability
                                └→ planner
planner ──fan out (parallel)──→ { knowledge, search, tool }
                                        │ all converge
                                        ▼
                                    reasoning ─┬─(optimisation wanted)→ optimization
                                               └→ validation
                        optimization → validation → reflection
reflection ─┬─(retry, budget left)→ { knowledge | search | tool | reasoning }   ⟲
            └→ explanation → observability → self_improving → END
```

* **Gates run first.** Guardrail and Security execute *before* the planner, so no
  untrusted text reaches an LLM and PII is redacted before planning.
* **Parallel fan-out is safe** because every concurrently-written state key carries an
  explicit reducer in `app/core/state.py` (`merge_metrics`, `merge_graph_context`,
  `merge_search_results`, `operator.add`). Single-owner keys use default overwrite.
* **Reflection is bounded** by `workflow_max_reflection_loops` with a forced exit.

### 3.3 Frontend data flow

```
main.jsx → <StrictMode> → Root.jsx
                            │  state: path, session (sessionStorage), theme (localStorage)
                            │  guards: unauthenticated → /login; role → landingFor()
                            ├─ lazy /login   → LoginPage
                            ├─ lazy /vehicle → VehicleDashboard
                            └─ lazy /        → App.jsx
                                               │
                                               │ owns 14 useState hooks:
                                               │ fleet, loading, activeNav, selectedTruck,
                                               │ origin, destination, routeRequest, search,
                                               │ statusFilter, sort, page, selectedRows,
                                               │ expandedId, toast, detailTruck, sidebar
                                               │
                                               ├─ fetchFleet() → /api/fleet/overview
                                               ├─ replanRoute() → /api/routes/replan
                                               │
                                               └─ renderPage() switch on activeNav
                                                   ├ OverviewPage  (props-driven, 22 props)
                                                   ├ FleetPage     (self-fetches profiles)
                                                   ├ RoutesPage    (self-fetches + plans)
                                                   ├ AnalyticsPage (self-fetches KPIs)
                                                   ├ SearchPage    (self-fetches network)
                                                   ├ NotificationsPage (self-fetches + mutates)
                                                   └ SettingsPage  (self-fetches health)
```

**State management:** no store, no react-router, no data-fetching library. Two patterns
coexist:

1. **Lifted state** — `App.jsx` owns fleet data and passes 22 props to `OverviewPage`.
2. **Page-local fetch** — every other page runs its own `useEffect` + `AbortController`.

Pattern 2 is the healthier one. Pattern 1 is why `App.jsx` is 414 lines and why
`OverviewPage` has a 22-prop signature.

---

## 4. API surface (38 endpoints, counted from the generated OpenAPI schema)

| Router | Prefix | Endpoints | Consumed by frontend |
| --- | --- | --- | --- |
| `health` | `/api` | `/health`, `/ready` | ✅ `/health` |
| `chat` | `/api` | `/roles`, `/chat`, `/chat/stream` | ❌ none |
| `routing` | `/api/routes` | `/network`, `/plan`, `/locations`, `/candidates`, `/algorithms`, `/model-card`, `/scenario`, `/replan`, `/incidents/generate` | ✅ 7 of 9 |
| `fleet` | `/api/fleet` | `/overview`, `/profiles`, `/profiles/{id}` | ✅ 2 of 3 |
| `graph` | `/api/graph` | `/schema`, `/query`, `/overview`, `/search` | ❌ none |
| `tools` | `/api/tools` | ``, `/execute` | ✅ list only |
| `constraints` | `/api/constraints` | `/profile`, `/catalogue`, `/evaluate` | ✅ 2 of 3 |
| `observability` | `/api/observability` | `/metrics`, `/kpis`, `/runs`, `/outcomes`, `/security-audit` | ✅ 3 of 5 |
| `monitor` | `/api/routes/monitor` | `/start`, `/status`, `/{id}`, `/{id}/events`, `/{id}/poll` | ✅ `/status` declared, never called |
| `mcp` | `/api/mcp` | `/tools`, `/call` | ❌ none |

**Integration verdict:** every endpoint the dashboard calls exists with a matching shape,
and `data/fleet.js` normalises snake_case → camelCase in exactly one place
(`normaliseTruck`). The frontend/backend contract is sound. The gap is the reverse —
the agent workflow (`/api/chat`), the knowledge graph explorer (`/api/graph`) and the MCP
tool surface are fully built and completely unreachable from the UI.

---

## 5. Dependency graph

### Package level (machine-derived from AST imports)

```
app.core        → (nothing)                                    ← foundation
app.llm         → app.core
app.security    → app.core
app.observability → app.core
app.kg          → app.core, app.llm
app.search      → app.core, app.kg
app.routing     → app.core, app.domain
app.domain      → app.core, app.kg, app.agents  ⚠ upward edge
app.mcp         → app.core, app.kg, app.domain, app.routing, app.search,
                  app.security, app.agents  ⚠ upward edge, scripts.*  ⚠
app.agents      → everything below it + app.mcp  ⚠
app.workflow    → app.core, app.agents
app.api         → all of the above + scripts.*  ⚠
```

Rendered as layers, with violations marked:

```
   ┌──────────────────────────── app.api ────────────────────────────┐
   │                    (10 routers, 38 endpoints)                    │
   └───┬──────────────────────────┬───────────────────────┬───────────┘
       │                          │                       │
  app.workflow               app.agents ◀───────┐    scripts.*  ⚠ (api imports a script)
       │                     │   ▲              │
       └────────────────────▶│   │              │
                             ▼   │              │
                      ┌─ app.mcp ┘  ⚠ CYCLE     │
                      │      │                  │
                      ▼      ▼                  │
              app.routing  app.domain ──────────┘  ⚠ CYCLE (domain → agents)
                      │      │
                      └──┬───┘
                         ▼
              app.kg → app.llm → app.core
```

### Confirmed cycles

| Cycle | Mechanism | Root cause |
| --- | --- | --- |
| `app.agents.optimization` ↔ `app.mcp.builtin` | Real module-level cycle, broken only by a function-local `import` inside `builtin.py` | `optimization.py` imports `weather_lookup` at module level; `builtin.py` needs `_path_to_candidate` back |
| `app.agents` ↔ `app.domain` | `domain/fleet_ops.py` does a function-local import of `app.agents.optimization` | Same |

**Single root cause for both:** `_path_to_candidate` — a 12-line pure `RoutePath →
RouteCandidate` mapper with no agent, LLM or I/O dependency — lives in the *agent* layer
but has **four** consumers: the API layer, the domain layer, the MCP tool layer, and the
tests. Three of them work around the cycle with deferred imports. It is domain logic
sitting one layer too high, and it is the reason the dependency graph is not acyclic.

### Frontend dependency graph

```
main.jsx
  └─ Root.jsx ──────────── config/demoAuth.js
       ├─ pages/LoginPage ─ components/login/{LoginForm, LoginVisual, LogisticsScene}
       ├─ pages/VehicleDashboard ─ components/{FleetMap, driver/DriverRail,
       │                                       vehicle/NavigationOverlay, vehicle/TripTimeline}
       └─ App.jsx ─ components/{Header, Sidebar, Toast}
                   └─ pages/* ─ components/{FleetMap, MetricCard, AlertPanel,
                                            TrackingTable, TruckDetailsDrawer, PageState}
                                            └─ data/fleet.js  ← single API client, no cycles
```

Clean and acyclic. `data/fleet.js` is the only module touching `fetch`, which is the
right shape.

---

## 6. Component hierarchy

```
Root                                      routing, session, theme  (Suspense boundary)
├── LoginPage                             role selection
│   ├── LoginForm
│   └── LoginVisual → LogisticsScene      react-three-fiber 3D scene
├── VehicleDashboard                      driver console
│   ├── FleetMap (985 LOC)                Leaflet + Google overlay
│   ├── NavigationOverlay
│   ├── TripTimeline
│   └── DriverRail
└── App                                   admin shell
    ├── Sidebar                           nav, alert badge, collapse
    ├── Header                            search, theme, notifications, sign-out
    ├── Toast                             transient notifications
    └── <one of 7 pages>
        └── OverviewPage
            ├── MetricCard ×4
            ├── FleetMap
            ├── TruckDetailsDrawer
            ├── AlertPanel
            └── TrackingTable
```

`FleetMap` at 985 lines is the single largest frontend module and does map init, marker
management, route drawing, Google polyline overlay and fleet controls in one component.

---

## 7. What is genuinely good here

Worth stating explicitly, because the fix list below should not obscure it:

1. **Determinism is architecturally enforced**, not just intended. The engine cannot call
   Google or an LLM; the LLM cannot invent a road because `build_path` verifies every leg.
2. **Provenance is carried in the payloads.** `derived: true`, `position_source`,
   `baseline_source`, `observed` on every delay factor, `unverifiable` on every constraint
   report. The UI can be honest because the API is.
3. **The delay model refuses to fake precision.** It is a factor model with per-factor
   evidence and a confidence that drops when inputs are assumed — plus a plausibility gate
   that rejects live readings implying impossible speeds.
4. **Concurrent LangGraph state is correctly reduced.** Every fan-out key has a reducer.
   This is the most common way multi-agent graphs corrupt state, and it is handled.
5. **Cypher is sanitised** and the knowledge agent is read-only; only `/api/routes/scenario`
   mutates incident state.
6. **178 tests pass** and cover constraints, routing, delay, security and MCP.

---

## 8. Findings summary

Severity: **S1** = broken in production / security · **S2** = latent crash or wrong output
· **S3** = maintainability, performance, polish.

| # | Sev | Area | Finding |
| --- | --- | --- | --- |
| 1 | **S1** | Security | `.env` is tracked in git with a live LLM API key, Neo4j password and Google Maps key |
| 2 | **S1** | API | `/api/routes/monitor/start` cannot succeed — `RouteCandidate` built without required `rank`, and `VehicleContext(profile=…)` is not a field |
| 3 | **S1** | Frontend | `RoutesPage` crashes the page when a plan request fails after a successful one (`plan` nulled, `selected` retained, then `plan.planning` dereferenced) |
| 4 | **S2** | Architecture | `_path_to_candidate` in the agent layer creates two import cycles; 3 of 4 call sites use deferred imports to hide it |
| 5 | **S2** | Routing | `engine.plan` annotates `shipment: ShipmentContext` but never imports it — `NameError` under any runtime type introspection |
| 6 | **S2** | Routing | ML `expected_delay_minutes` is computed once per plan from `len(incidents_by_location)` (all incident locations, **including inactive**) then added to **every edge** — biases selection toward fewer-hop routes |
| 7 | **S2** | API | `/api/routes/monitor/{id}/events` and `/poll` reach into `monitor._routes` private state; `deregister` appends an event to an already-popped record |
| 8 | **S2** | Config | `security_pii_action` is read in two places but does not exist in `Settings`; `/api/health` advertises it as configurable while it is permanently `"mask"` |
| 9 | **S2** | Frontend | `SettingsPage` renders the literal string `"undefined…"` when the tools payload has not resolved |
| 10 | **S3** | Performance | Bulk incident toggle issues one `POST /scenario` per incident, each rebuilding the full network payload with coordinate resolution — O(n) heavy round trips |
| 11 | **S3** | Performance | `build_fleet` plans 6 corridors strictly sequentially; each awaits graph planning + live traffic |
| 12 | **S3** | Logic | A corridor with no connected path yields a depot truck but **no alert** — the `continue` skips `_alerts_for` |
| 13 | **S3** | Cleanup | `dashboard/dashboard/`, dead `logipilot-console` launch config, 3 committed Playwright PNGs, `test.json` |
| 14 | **S3** | Frontend | Page fetch effects call `setLoading(false)` in `.finally()` even on abort — state write after unmount |
| 15 | **S3** | Coverage | `/api/chat`, `/api/graph/*` and `/api/mcp/*` are fully implemented and completely unreachable from the UI |

---

## 9. Remediation plan

Ordered so that each iteration is independently shippable and verified before the next
begins. Every step preserves existing endpoint paths, request shapes and response shapes.

| Iteration | Contents | Findings |
| --- | --- | --- |
| **1 — API integrity, module boundaries, secrets** | Repair the monitoring router; break both import cycles by relocating `path_to_candidate` to the domain layer; fix the `ShipmentContext` import; fix the `RoutesPage` crash and the `undefined…` render; untrack `.env`; add the missing `security_pii_action` setting | 1, 2, 3, 4, 5, 7, 8, 9 |
| **2 — Routing cost-model correctness** | The ML delay term (finding 6). This changes which route wins, so it is deliberately isolated in its own iteration with before/after candidate comparison | 6 |
| **3 — Performance** | Parallelise `build_fleet` corridors; add a bulk incident endpoint so the UI stops N-times rebuilding the network; memoise the network projection | 10, 11 |
| **4 — Frontend structure** | Split `FleetMap` (985 LOC); extract a shared `useApiResource` hook to replace 6 copies of the fetch/abort/loading triple; reduce the `OverviewPage` prop surface | 14 + `App.jsx` size |
| **5 — Cleanup and reach** | Remove dead artefacts; wire the agent console and graph explorer to the existing endpoints | 12, 13, 15 |

---

## 10. Constraints this review commits to

* No endpoint path, query parameter, request body or response field is removed or renamed.
* Business logic changes are confined to iteration 2 and are called out explicitly with
  before/after evidence.
* `_path_to_candidate` keeps a working import from `app.agents.optimization` so the
  existing tests and any external caller continue to work.
* The 178-test suite is run after every group of changes.

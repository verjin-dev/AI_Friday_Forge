# LogiPilot AI — Setup

Getting the platform running on a new machine. Roughly 15 minutes, most of it
waiting on installs.

At any point you can run the environment check, which tells you exactly what is
missing and how to fix it:

```bash
python scripts/verify_setup.py
```

---

## 0. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | Built and tested on 3.12.8 |
| Node.js | 20+ | Tested on 22.15.1, npm 10.9.2 |
| Neo4j | 5.x | Community Edition is fine; Desktop or Docker both work |

You also need:

- an **OpenAI-compatible LLM endpoint** (base URL + key) — only for the
  12-agent chat workflow. Route planning, constraints and the map all work
  without it.
- a **Google Maps API key** — optional. Without it the map shows an error panel
  and delay prediction falls back to road-class estimates, saying so.

---

## 1. Get the code and enter it

```bash
cd "Regional Finals"
```

---

## 2. Create the Python environment

Windows PowerShell:

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
python3 -m venv .venv && source .venv/bin/activate
```

Then install:

```bash
python -m pip install --upgrade pip
```

```bash
python -m pip install -r requirements.txt
```

---

## 3. Start Neo4j

**Docker** is the quickest:

```bash
docker run -d --name logipilot-neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/YourPassword neo4j:5
```

Or use **Neo4j Desktop**: create a local DBMS, set a password, press Start.

Confirm it is listening on port 7687 before continuing — the browser console at
<http://localhost:7474> is the easiest check.

> **Use `bolt://`, not `neo4j://`.** The `neo4j://` scheme is for clusters and
> Aura. Against a standalone server it fails with *"Unable to retrieve routing
> information"*. This catches people out constantly.

---

## 4. Configure

```bash
cp .env.example .env
```

Then edit `.env`:

| Variable | Required | Notes |
|---|---|---|
| `NEO4J_URI` | yes | `bolt://127.0.0.1:7687` |
| `NEO4J_USERNAME` | yes | usually `neo4j` |
| `NEO4J_PASSWORD` | yes | whatever you set in step 3 |
| `OPENAI_API_KEY` | for chat | leave blank to skip the agent workflow |
| `OPENAI_BASE_URL` | for chat | your gateway, e.g. `https://your-gateway` |
| `OPENAI_MODEL` | for chat | model name on that gateway |
| `OPENAI_VERIFY_SSL` | no | set `false` behind a TLS-intercepting proxy |
| `EXTERNAL_VERIFY_SSL` | no | same, for public APIs (weather, maps) |
| `GOOGLE_MAPS_API_KEY` | no | enables live traffic in delay prediction |
| `LANGSMITH_TRACING` | no | `true` plus `LANGSMITH_API_KEY` for tracing |

### Google Cloud APIs

If you supply a Google key, enable **all three** on the project — they are
separate products and enabling one does not enable the others:

| API | Used by |
|---|---|
| Routes API | backend live traffic (`app/domain/live_traffic.py`) |
| Maps JavaScript API | rendering the dashboard map |
| Directions API | drawing routes and alternatives on the map |

A missing one shows up as a `403` in the response, and the platform degrades
rather than failing.

---

## 5. Put the data in place

Drop the CSV files anywhere under `data/` — the loader searches recursively and
accepts either naming convention:

| Dataset | Accepted file names |
|---|---|
| Locations | `location_nodes.csv` or `locations.csv` |
| Incidents | `incident_nodes.csv` or `incidents.csv` |
| Roads | `location_relationships.csv` or `road_connections.csv` |
| Incident links | `incident_locations.csv` |
| Alternates | `alternate_routes.csv` |
| Vehicle profiles | `missing_data_template.csv` (optional) |

Column names are flexible too — `from`/`from_location`, `to`/`to_location`,
`extra_distance`/`extra_distance_km` all work.

Then load:

```bash
python scripts/load_graph.py
```

Add `--reset` to wipe existing `Location`, `Incident` and `VehicleProfile`
nodes first. Use `--dir path/to/csvs` to load from elsewhere.

The loader prints which file it picked for each dataset and finishes with the
active incident list — a quick sanity check that the right data landed.

---

## 6. Check the environment

```bash
python scripts/verify_setup.py
```

It checks Python, packages, `.env`, the CSV files, Neo4j connectivity, whether
the graph is actually loaded, that pathfinding works on your data, and the
frontend prerequisites. It never prints a secret. Exit code 0 means ready.

---

## 7. Install the frontends

```bash
npm install --prefix dashboard
```

```bash
npm install --prefix frontend
```

For the map, give the dashboard a **browser** key (separate from the backend
key, and referrer-restricted):

```bash
cp dashboard/.env.example dashboard/.env.local
```

Then set `VITE_GOOGLE_MAPS_API_KEY` in `dashboard/.env.local`. That file is
git-ignored.

---

## 8. Run it

Three processes. Use three terminals, or run the first two in the background.

**Backend** — must be first, the UIs proxy to it:

```bash
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8010
```

**Operations dashboard:**

```bash
npm run dev --prefix dashboard
```

**Agent console** (optional — chat and the 12-agent timeline):

```bash
npm run dev --prefix frontend
```

| Surface | URL |
|---|---|
| Login | <http://localhost:5174/login> |
| Operations dashboard | <http://localhost:5174/> |
| Vehicle console | <http://localhost:5174/vehicle> |
| Agent console | <http://localhost:5173> |
| API docs | <http://127.0.0.1:8010/docs> |

**Demo sign-in:** `ops` / `logipilot` for the dashboard, `driver` / `logipilot`
for the vehicle console. This is a front-end role gate only — the credentials
are in the bundle and the API is unauthenticated. Replace
`dashboard/src/config/demoAuth.js` before any real deployment.

---

## 9. Confirm it works

```bash
python -m pytest
```

91 tests, entirely offline — no Neo4j, LLM or Google calls.

A live end-to-end check:

```bash
curl "http://127.0.0.1:8010/api/routes/plan?origin=Attingal&destination=Kollam"
```

You should get several routes, each with a constraint report and a delay
prediction. Add `&profile=P001` to activate the vehicle constraints (driver
hours, licence class, delivery window, SLA).

---

## Troubleshooting

**`Unable to retrieve routing information`**
`NEO4J_URI` uses `neo4j://`. Change it to `bolt://`.

**`Address already in use` on 8010**
Something else has the port. Either free it, or run on another port and update
the `proxy.target` in `dashboard/vite.config.js` and `frontend/vite.config.js`.

**Dashboard says "Map unavailable"**
No `VITE_GOOGLE_MAPS_API_KEY` in `dashboard/.env.local`, the key is
referrer-restricted away from `localhost`, or the Maps JavaScript API is not
enabled on the project.

**Delay notes say "Live reading rejected"**
The Routes API returned a journey far longer than the graph's own distance,
usually an ambiguous place name. The platform discards it and falls back to the
road-class estimate. Add the town to `STATIC_COORDINATES` in
`app/domain/geo.py` if it recurs.

**Certificate errors on outbound calls**
Behind a TLS-intercepting proxy. Set `OPENAI_VERIFY_SSL=false` and
`EXTERNAL_VERIFY_SSL=false`. Do not ship that way.

**Everything is "no compliant route"**
Expected if many blocking incidents are active. Check
<http://localhost:5174/> → Notifications, and clear one to see routes reappear.
Only `Critical` blocks by default; change with `BLOCKING_SEVERITIES` in `.env`.

**`/api/fleet/overview` is slow**
It plans every corridor and calls live traffic for each. 10–20 seconds on a
cold start is normal; the deterministic `/api/routes/plan` is sub-second.

---

## What runs without what

| Missing | Still works | Lost |
|---|---|---|
| LLM key | routing, constraints, delay, map, all dashboard pages | `/api/chat`, the agent console |
| Google key | everything | live traffic; delay uses road-class estimates |
| Neo4j | nothing useful | this is the only hard requirement |
| `missing_data_template.csv` | routing and network constraints | capacity, driver hours, licence, SLA checks |

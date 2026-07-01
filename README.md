# Urban Twin — Halasuru, Bengaluru

A multi-agent urban infrastructure digital twin that simulates real-time failure cascades across the **Power**, **Water**, and **Telecom** networks of the Halasuru / Indiranagar area of Bengaluru. Built on real OpenStreetMap data, it lets you inject disasters (floods, node failures), watch cascades propagate live across all three networks simultaneously, and run Monte Carlo risk analysis to find the most vulnerable assets.

---

## What It Does

The system models ~2 km of real Bengaluru infrastructure as three interconnected graphs, each governed by actual physics/engineering equations.

### Power Network

Pulls real substations, transformers, generators, and plants from OSM. Each node carries real electrical attributes — rated capacity (MW), voltage (kV), impedance (Ω), relay TMS, thermal limits — based on BESCOM 11kV distribution specs and IEEE-RTS-96 standards. When a substation fails, the agent runs DC power flow, reroutes transformers to their backup substation, checks IEC 60255-151 relay trip times on each feeder line, models Joule heating, and cascades failures downstream.

### Water Network

Models pump stations, water towers, and pipe junctions. Pump stations are powered by the grid — lose power (from a power cascade), and the pump switches to backup generator for a limited time, then pressure decays exponentially. Water towers have finite storage and drain when their fill source goes offline. Pipe junctions track pressure using the Hazen-Williams equation and can burst when pressure drops critically low. Topology mirrors BWSSB Indiranagar infrastructure with physics-accurate synthetic parameters (BWSSB pipe data is not public).

### Telecom Network

15 real cell tower locations (ground towers + wall-mounted) with actual operator data — Jio, Airtel, Vi, BSNL — running 2G/3G/4G/5G on real frequency bands (850/900/1800/2100/2300/3500 MHz). Coverage radii are computed from the Friis transmission equation with free-space path loss. Each tower has a battery backup; lose grid power and it runs on battery until depletion, then goes dark.

### Cross-Network Cascades

The three networks are not isolated. A substation failure triggers a feeder line drop → water pump loses grid power → switches to generator (limited hours) → pressure decay begins. The same feeder drop → cell tower loses grid → battery countdown begins. All of this propagates through an async event bus in real time, tick by tick (each tick = 5 real minutes of simulation time).

### Monte Carlo Risk Engine

Run N independent simulation runs (up to 500) with randomised physics parameters to get failure probability distributions. Outputs: probability of transformer failure, water pump failure, pipe burst, telecom tower going dark, cross-network cascade, critical pressure drops. Shows which specific assets are most vulnerable across the ensemble.

### Live Map + Event Feed

A Next.js frontend renders all three networks on an interactive Leaflet map. A live WebSocket feed shows every event in real time — node failures, cascade triggers, recoveries. Click any node to fail or restore it manually, or drop a flood anywhere on the map to fail all nodes in the radius.

---

## Tech Stack

**Backend:** Python 3.11+, FastAPI, uvicorn, asyncio pub/sub event bus (no Redis), OSMnx, GeoPandas, NetworkX, Shapely, SciPy, NumPy

**Frontend:** Next.js 14 (App Router), TypeScript, Leaflet / React-Leaflet, Lucide React

**Data source:** OpenStreetMap via OSMnx (real Halasuru geometry, EPSG:32643 UTM projection)

---

## Prerequisites

- Python 3.11+ with pip
- Node.js 18+ with npm
- Windows with PowerShell (the start script is `.ps1`)
- Internet access for the first run (OSMnx pulls live from OpenStreetMap)

---

## First-Time Setup

**Create and activate virtual environment:**

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**Install Python dependencies:**

```powershell
pip install -r requirements.txt
pip install -r requirements-api.txt
```

**Install frontend dependencies:**

```powershell
cd frontend
npm install
cd ..
```

---

## First-Time Data Build

Before the servers can start, you need to build the graph files from OSM and generate the enriched JSON files. Run everything from inside `src/`.

```powershell
cd src
```

**Step 1 — Build the road graph and building footprints (everything else depends on this):**

```powershell
python graph_builder.py
```

Pulls the road network and building footprints for a 2 km radius around Halasuru metro station from OpenStreetMap. Saves `graphs/road_graph.graphml`, `graphs/road_nodes.gpkg`, `graphs/road_edges.gpkg`, and `data/buildings.gpkg`.

**Step 2 — Build the three infrastructure graphs:**

```powershell
python power.py
python telecom.py
python water_network.py
```

- `power.py` — queries OSM for substations and transformers, assigns BESCOM-spec physics attributes, builds road-routed feeder edges, saves `graphs/power_nodes.gpkg`, `graphs/power_edges.gpkg`, `graphs/power.json`
- `telecom.py` — places 15 tower nodes with Friis-equation coverage radii per operator and band, saves `graphs/telecom_nodes.gpkg`, `graphs/telecom.json`
- `water_network.py` — queries OSM for water infrastructure (falls back to synthetic nodes when OSM data is sparse), builds pipe topology, saves `graphs/water_nodes.gpkg`, `graphs/water_edges.gpkg`, `graphs/water.json`

**Step 3 — Generate the enriched JSON files the API server reads at startup:**

```powershell
python power_agent.py
python telecom_agent.py
python water_agent.py
```

These run the agents once in standalone mode to write the enriched node state JSON files. Only required the first time, or after rebuilding the graphs.

After these steps all `.gpkg` and `.json` files are in `src/graphs/` and `src/data/`. You do not need to run the builders again unless you want fresh OSM data.

---

## Running the Application

From the **project root**, run:

```powershell
.\start.ps1
```

This opens two PowerShell windows:

- **FastAPI backend** → `http://localhost:8000`
- **Next.js frontend** → `http://localhost:3000`

Open `http://localhost:3000` in your browser.

---

## Using the Application

### Dashboard (`/`)

Overview of the loaded infrastructure — total nodes, edges, cross-network dependencies, and quick navigation links.

### Combined Map (`/map/combined`)

The main operational view. All three networks rendered simultaneously on a Leaflet map of Bengaluru. Click any node to inspect its properties (type, health, voltage / pressure / battery level, operational status). From here:

- **Fail a node** — click a node → Fail Node button. The agent propagates cascades in real time.
- **Restore a node** — click a failed node → Restore Node. Recovery events flow back through the bus.
- **Inject a flood** — set lat/lon and radius, hit inject. All nodes in the radius fail simultaneously, cascades begin.
- **Recover a flood zone** — same interface, recovers all nodes in the radius.
- **Live event feed** — right-side panel streams every event (NODE_FAILED, FEEDER_LINE_DROPPED, PUMP_ON_BACKUP, CELL_TOWER_BATTERY, CASCADE_TRIGGERED, etc.) as they arrive via WebSocket.

### Monte Carlo Analysis (`/montecarlo`)

- Select specific nodes to fail (power, water, or telecom), or choose "fail all substations" for worst-case
- Set number of runs (10–500) and simulation ticks (10–120)
- Toggle flood scenario on/off
- Results: failure probability per risk category (colour-coded LOW / MED / HIGH), average failure counts, top 5 most vulnerable individual assets (transformers, pumps, towers)

### Simulation Controls (`/simulation`)

Manual flood injection and bulk scenario triggers.

### Replay (`/replay`)

Step through a saved event log from any past simulation run. Inspect full cascade chains, jump to any tick, filter by network.

### Per-network maps

`/map/power`, `/map/water`, `/map/telecom` — individual network views with full node/edge detail.

---

## How the Simulation Works

When the FastAPI server starts, it boots a continuous async simulation:

1. **Orchestrator** manages three agents (PowerAgent, WaterAgent, TelecomAgent) and the simulation clock
2. **Event Bus** — pure asyncio pub/sub. Each agent subscribes to the event types it cares about and publishes events it generates. No Redis, no external broker — zero extra dependencies.
3. **Per tick**: the orchestrator publishes a `SIMULATION_TICK` event. All three agents process it concurrently, run their physics equations, update node/edge state, and publish any resulting events (failures, degradations, cascades, recoveries)
4. **WebSocket bridge** — the API server subscribes to the event bus and fans every event out to all connected browsers in real time
5. **Event log** — every event is appended to `data/events.jsonl` for replay and audit

Each simulation tick = 5 real minutes. Physics per tick:

- **Power**: DC flow recalculation → IEC 60255-151 relay trip time check → Joule heating model → thermal degradation
- **Water**: exponential pressure decay (k = 0.085/tick) → Hazen-Williams pipe flow → storage level drain
- **Telecom**: battery drain (kWh/tick based on tower power draw) → signal coverage loss on depletion

---

## Disaster Scenarios

| Scenario | Effect |
|---|---|
| Fail a substation | Transformers reroute to backup, feeders may trip, downstream pumps and towers lose power, cell towers switch to battery |
| Fail all substations | Entire area loses grid — all water pumps on generator countdown, all telecom on battery countdown |
| Flood injection (map) | All infrastructure nodes inside the radius fail simultaneously, cascades propagate outward |
| Fail a water pump | Pressure decay in downstream junctions, connected towers begin draining |
| Fail a telecom tower | Coverage hole, SIGNAL_LOSS event |
| Monte Carlo 500 runs | Statistical outcome distribution, identify p90 failure probabilities, spot most vulnerable assets |

---

## API Reference

| Endpoint | Description |
|---|---|
| `GET /api/health` | Liveness check, loaded node counts per network |
| `GET /api/graphs/{network}` | GeoJSON nodes + edges for `power`, `water`, or `telecom` |
| `GET /api/graphs/combined/all` | All three networks in one GeoJSON response |
| `GET /api/dependencies` | Cross-network dependency edges |
| `GET /api/stats` | Aggregate node, edge, and dependency counts |
| `GET /api/events/history` | Full event log from `data/events.jsonl` |
| `GET /api/live-state` | Current health state of all nodes from all agents |
| `GET /api/montecarlo/nodes` | All nodes available to target in Monte Carlo |
| `POST /api/simulation/flood` | Inject flood `{ lat, lon, radius_m }` |
| `POST /api/simulation/recover` | Recover all nodes in a radius |
| `POST /api/simulation/fail-node` | Fail a specific node `{ node_id }` |
| `POST /api/simulation/recover-node` | Restore a specific node `{ node_id }` |
| `POST /api/montecarlo/run` | Run Monte Carlo `{ fail, runs, ticks, flood, no_cascade, fail_all_substations }` |
| `WS /ws/events` | WebSocket — live stream of all simulation events |

---

## Monte Carlo from the Terminal

```powershell
cd src

# See all node names and IDs
python monte_carlo.py --list-nodes

# Fail Substation 1 — 100 runs, 60 ticks (5 hours simulated)
python monte_carlo.py --fail "Substation 1" --runs 100 --ticks 60

# Worst case — fail all substations at once
python monte_carlo.py --fail-all-substations --runs 200

# Add a flood event on top of the failure
python monte_carlo.py --fail "Substation 1" --flood --runs 100

# Baseline run — no cross-network cascade forcing
python monte_carlo.py --fail "Substation 1" --no-cascade --runs 100

# Compare two scenarios with identical random seeds
python monte_carlo.py --fail "Substation 1" --runs 500 --seed-base 0
python monte_carlo.py --fail "Substation 2" --runs 500 --seed-base 0
```

Results saved to `data/monte_carlo_results.csv`.

---

## Replay a Simulation

```powershell
cd src
python replay.py data/events.jsonl
```

Interactive commands in the replayer:

| Command | Action |
|---|---|
| `n` | Next event |
| `p` | Previous event |
| `N10` | Skip forward 10 events |
| `t5` | Jump to tick 5 |
| `c` | Show full cascade chain from current event |
| `s` | Summary — totals by type, by network, max cascade depth |
| `?power` | Filter view to power events only |
| `q` | Quit |

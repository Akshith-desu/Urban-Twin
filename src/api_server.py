"""
Urban Twin - FastAPI Bridge Server
Exposes graph data, live WebSocket events, and interfaces with Simulation Agents.
Run from src/: uvicorn api_server:app --reload --port 8000
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import geopandas as gpd
import json, os, random, asyncio, math, time, uuid
from typing import Dict, List, Optional, Set
from pathlib import Path
import numpy as np
from event_bus import EventBus
from event_schema import EventType, Network, Event
from orchestrator import SimulationOrchestrator
# api_server.py — add to existing import line:
from event_schema import EventType, Network, Event, user_fail_node, user_restore_node
import sys, csv as _csv

app = FastAPI(title="Urban Twin API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR   = Path(__file__).parent
GRAPHS_DIR = BASE_DIR / "graphs"
DATA_DIR   = BASE_DIR / "data"

# ROAD REMOVED entirely per user request
NETWORKS   = ["power", "water", "telecom"]

NETWORK_COLORS = {
    "power":   "#fbbf24",
    "water":   "#60a5fa",
    "telecom": "#c084fc",
}

_graph_cache: Dict[str, dict] = {}
_dep_cache: List[dict] = []

bus = EventBus()
orch = SimulationOrchestrator(bus)


# ── WebSocket Manager ─────────────────────────────────────────────────────────

class WSManager:
    def __init__(self):
        self.connections: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.add(ws)

    def disconnect(self, ws: WebSocket):
        self.connections.discard(ws)

    async def broadcast(self, msg: dict):
        text = json.dumps(msg)
        dead = set()
        for ws in self.connections:
            try:
                await ws.send_text(text)
            except Exception:
                dead.add(ws)
        self.connections -= dead

manager = WSManager()


# ── WebSocket Endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean(val):
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return None if math.isnan(float(val)) else float(val)
    if isinstance(val, (np.bool_,)):
        return bool(val)
    return val

def sanitize_props(props: dict) -> dict:
    return {k: _clean(v) for k, v in (props or {}).items()}

def sanitize_fc(fc: dict) -> dict:
    for f in fc.get("features", []):
        if f.get("properties"):
            f["properties"] = sanitize_props(f["properties"])
    return fc

def load_network(network: str) -> dict:
    node_candidates = [
        GRAPHS_DIR / f"{network}_nodes_enriched.gpkg",
        GRAPHS_DIR / f"{network}_nodes.gpkg",
    ]
    edge_candidates = [GRAPHS_DIR / f"{network}_edges.gpkg"]

    nodes_gdf, edges_gdf = None, None
    for p in node_candidates:
        if p.exists():
            nodes_gdf = gpd.read_file(p).to_crs("EPSG:4326")
            break
    for p in edge_candidates:
        if p.exists():
            edges_gdf = gpd.read_file(p).to_crs("EPSG:4326")
            break

    # Load JSON for enrichment
    json_path = GRAPHS_DIR / f"{network}.json"
    json_data = {}
    if json_path.exists():
        try:
            with open(json_path) as f:
                raw = json.load(f)
                json_data = {str(n.get("node_id", "")): n for n in raw.get("nodes", [])}
        except Exception as e:
            print(f"  Error loading enrichment JSON {json_path}: {e}")

    def to_fc(gdf, is_nodes=False):
        if gdf is None or len(gdf) == 0:
            return {"type": "FeatureCollection", "features": []}
        fc = json.loads(gdf.to_json())
        if is_nodes:
            for f in fc.get("features", []):
                props = f.get("properties", {})
                nid = str(props.get("node_id") or props.get("id") or "")
                if nid in json_data:
                    enriched = {**json_data[nid], **props}
                    f["properties"] = enriched
        return sanitize_fc(fc)

    nodes_fc = to_fc(nodes_gdf, is_nodes=True)
    edges_fc = to_fc(edges_gdf)
    return {
        "nodes": nodes_fc,
        "edges": edges_fc,
        "node_count": len(nodes_fc["features"]),
        "edge_count": len(edges_fc["features"]),
    }

# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    print("Urban Twin API starting\u2026")
    for net in NETWORKS:
        try:
            _graph_cache[net] = load_network(net)
            print(f"  {net}: {_graph_cache[net]['node_count']} nodes, {_graph_cache[net]['edge_count']} edges")
        except Exception as e:
            print(f"  WARNING {net}: {e}")
            _graph_cache[net] = {"nodes": {"type":"FeatureCollection","features":[]}, "edges": {"type":"FeatureCollection","features":[]}, "node_count":0, "edge_count":0}

    dep_path = DATA_DIR / "dependency_edges.json"
    if dep_path.exists():
        with open(dep_path) as f:
            _dep_cache.extend(json.load(f))
        print(f"  Dependencies: {len(_dep_cache)}")

    await orch.start()
    print("Orchestrator started.")

    # MANDATORY: Subscribe the bridge to the bus so it gets a queue!
    bus.subscribe("api_server_ws", [t for t in EventType])

    async def bridge_to_ws():
        print("WebSocket Bridge: Waiting for events...")
        async for event in bus.get_events("api_server_ws", [t for t in EventType]):
            print(f"DEBUG: Bridge received {event.event_type} from {event.source_network.value} for {event.node_id}")
            
            # Only broadcast events for networks we are tracking (power, water, telecom)
            if event.source_network.value not in NETWORKS and event.source_network.value != "system":
                print(f"DEBUG: Bridge FILTERED OUT {event.source_network.value} (not in {NETWORKS})")
                continue
            
            # For cascade events, also verify the target network is one we track
            if event.event_type == EventType.CASCADE_TRIGGERED:
                target_net = event.metadata.get("target_network")
                if target_net not in NETWORKS:
                    print(f"DEBUG: Bridge FILTERED CASCADE to {target_net}")
                    continue

            print(f"DEBUG: Bridge BROADCASTING {event.event_type} for {event.node_id}")
            await manager.broadcast({"type": "event", "data": event.to_dict()})

    asyncio.create_task(bridge_to_ws())
    print("Bridge to WS active.")
    print("Startup complete.")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "loaded": {n: _graph_cache[n]["node_count"] for n in NETWORKS}}

@app.get("/api/graphs/{network}")
async def get_network(network: str):
    if network not in NETWORKS:
        raise HTTPException(404, f"Unknown network: {network}")
    d = _graph_cache[network]
    return JSONResponse({"nodes": d["nodes"], "edges": d["edges"],
                         "stats": {"node_count": d["node_count"], "edge_count": d["edge_count"]}})

@app.get("/api/graphs/combined/all")
async def get_combined():
    features = []
    stats = {}
    for net in NETWORKS:
        d = _graph_cache.get(net, {})
        for f in d.get("nodes", {}).get("features", []):
            f.setdefault("properties", {}).update({"_network": net, "_color": NETWORK_COLORS[net], "_ftype": "node"})
            features.append(f)
        for f in d.get("edges", {}).get("features", []):
            f.setdefault("properties", {}).update({"_network": net, "_color": NETWORK_COLORS[net], "_ftype": "edge"})
            features.append(f)
        stats[net] = {"node_count": d.get("node_count", 0), "edge_count": d.get("edge_count", 0)}
    return JSONResponse({"type": "FeatureCollection", "features": features, "stats": stats})

@app.get("/api/dependencies")
async def get_deps():
    return JSONResponse([d for d in _dep_cache if d.get("to_network") != "road"])

@app.get("/api/events/history")
async def get_history():
    p = DATA_DIR / "events.jsonl"
    if not p.exists():
        return JSONResponse({"events": [], "total": 0})
    events = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return JSONResponse({"events": events, "total": len(events)})

@app.get("/api/stats")
async def get_stats():
    total_nodes = sum(_graph_cache[n]["node_count"] for n in NETWORKS)
    total_edges = sum(_graph_cache[n]["edge_count"] for n in NETWORKS)
    return JSONResponse({
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "dependencies": len(_dep_cache),
        "networks": {n: {"nodes": _graph_cache[n]["node_count"], "edges": _graph_cache[n]["edge_count"]} for n in NETWORKS},
    })


# ── Flood Injection ───────────────────────────────────────────────────────────

@app.post("/api/simulation/flood")
async def inject_flood(body: dict):
    lat = float(body.get("lat", 12.9762))
    lon = float(body.get("lon", 77.6265))
    radius_m = float(body.get("radius_m", 500))

    rad_deg = radius_m / 111_000
    affected_nodes = []
    node_names = {}
    for net in NETWORKS:
        for f in _graph_cache.get(net, {}).get("nodes", {}).get("features", []):
            g = f.get("geometry", {})
            if g.get("type") == "Point":
                c = g["coordinates"]
                d = math.hypot(c[1] - lat, c[0] - lon)
                if d < rad_deg:
                    props = f.get("properties", {})
                    nid = props.get("node_id")
                    if nid:
                        affected_nodes.append(nid)
                        n_type = str(props.get("node_type", "Node")).replace("_", " ").title()
                        n_name = props.get("name")
                        display_name = f"{n_type} {n_name}" if n_name and str(n_name) != str(nid) else f"{n_type} ({nid})"
                        node_names[nid] = display_name

    print(f"Injecting flood at {lat}, {lon} with radius {radius_m}m. Affected nodes: {len(affected_nodes)}")
    await orch.inject_scenario("flood", {"nodes": affected_nodes, "names": node_names})

    async def auto_step():
        for _ in range(5):
            await orch.run_step()
            await asyncio.sleep(1.0)

    asyncio.create_task(auto_step())
    return {"status": "injected", "lat": lat, "lon": lon, "radius_m": radius_m, "affected_count": len(affected_nodes)}


@app.post("/api/simulation/recover")
async def recover_nodes(body: dict):
    lat = float(body.get("lat", 12.9762))
    lon = float(body.get("lon", 77.6265))
    radius_m = float(body.get("radius_m", 500))
    rad_deg = radius_m / 111_000

    affected = []
    for net in NETWORKS:
        for f in _graph_cache.get(net, {}).get("nodes", {}).get("features", []):
            g = f.get("geometry", {})
            if g.get("type") == "Point":
                c = g["coordinates"]
                if math.hypot(c[1] - lat, c[0] - lon) < rad_deg:
                    nid = f.get("properties", {}).get("node_id")
                    if nid:
                        affected.append((str(nid), net))

    for nid, net in affected:
        await bus.publish(user_restore_node(Network(net), nid, tick=orch.current_tick))

    asyncio.create_task(orch.run_step())
    return {"status": "recovery_initiated", "affected_count": len(affected)}

# ── Manual Failure / Recovery ─────────────────────────────────────────────────

@app.post("/api/simulation/fail-node")
async def fail_node(body: dict):
    node_id = str(body.get("node_id", ""))
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")

    display_name = "Asset " + node_id
    source_network = Network.SYSTEM
    for net in NETWORKS:
        for f in _graph_cache.get(net, {}).get("nodes", {}).get("features", []):
            props = f.get("properties", {})
            if str(props.get("node_id", "")) == node_id or str(props.get("id", "")) == node_id:
                n_type = str(props.get("node_type", "Node")).replace("_", " ").title()
                n_name = props.get("name")
                display_name = f"{n_type} {n_name}" if n_name and str(n_name) != node_id else f"{n_type} ({node_id})"
                source_network = Network(net)
                break

    direct_event = {
        "event_id": str(uuid.uuid4())[:8], "timestamp": time.time(),
        "tick": orch.current_tick, "event_type": "NODE_FAILED",
        "source_network": source_network.value, "node_id": node_id,
        "node_name": display_name, "severity": 1.0,
        "affected_nodes": [], "cascade_depth": 0,
        "metadata": {"reason": "manual_injection"}
    }
    await manager.broadcast({"type": "event", "data": direct_event})

    # Deterministic — skips flood's per-type dice rolls entirely
    await bus.publish(user_fail_node(source_network, node_id, tick=orch.current_tick))

    async def run_steps():
        for _ in range(5):
            await orch.run_step()
            await asyncio.sleep(0.5)
    asyncio.create_task(run_steps())
    return {"status": "node_failed", "node_id": node_id, "name": display_name}


@app.post("/api/simulation/recover-node")
async def recover_node(body: dict):
    node_id = str(body.get("node_id", ""))
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")

    source_network = Network.SYSTEM
    for net in NETWORKS:
        for f in _graph_cache.get(net, {}).get("nodes", {}).get("features", []):
            props = f.get("properties", {})
            if str(props.get("node_id", "")) == node_id or str(props.get("id", "")) == node_id:
                source_network = Network(net)
                break

    direct_event = {
        "event_id": str(uuid.uuid4())[:8], "timestamp": time.time(),
        "tick": orch.current_tick, "event_type": "NODE_RECOVERED",
        "source_network": source_network.value, "node_id": node_id,
        "node_name": None, "severity": 0.0,
        "affected_nodes": [], "cascade_depth": 0,
        "metadata": {"reason": "manual_recovery"}
    }
    await manager.broadcast({"type": "event", "data": direct_event})

    await bus.publish(user_restore_node(source_network, node_id, tick=orch.current_tick))
    asyncio.create_task(orch.run_step())
    return {"status": "node_recovered", "node_id": node_id}

@app.get("/api/montecarlo/nodes")
async def mc_nodes():
    result = {}
    for net in NETWORKS:
        net_nodes = []
        for f in _graph_cache.get(net, {}).get("nodes", {}).get("features", []):
            p = f.get("properties", {})
            nid = p.get("node_id") or p.get("id")
            ntype = p.get("power") or p.get("node_type") or "tower"
            if nid:
                net_nodes.append({
                    "id": str(nid),
                    "name": p.get("name") or str(nid),
                    "type": ntype
                })
        result[net] = net_nodes
    return JSONResponse(result)

@app.get("/api/live-state")
async def live_state():
    state = {}
    for agent in orch.agents:
        net_key = agent.name.replace("_agent", "")
        if hasattr(agent, "get_all_states"):
            state[net_key] = agent.get_all_states()
    return JSONResponse(state)

import subprocess
from concurrent.futures import ThreadPoolExecutor
_mc_executor = ThreadPoolExecutor(max_workers=1)

@app.post("/api/montecarlo/run")
async def run_montecarlo_api(body: dict):
    fail_nodes = body.get("fail", [])
    runs       = max(10, min(int(body.get("runs", 100)), 500))
    ticks      = max(10, min(int(body.get("ticks", 60)), 120))
    flood      = bool(body.get("flood", False))
    no_cascade = bool(body.get("no_cascade", False))
    fail_all   = bool(body.get("fail_all_substations", False))

    cmd = [
        str(sys.executable),
        str(BASE_DIR / "monte_carlo_3.py"),
        "--runs", str(runs),
        "--ticks", str(ticks),
        "--seed-base", "0"
    ]
    if fail_all:
        cmd.append("--fail-all-substations")
    elif fail_nodes:
        cmd += ["--fail"] + [str(n) for n in fail_nodes]
    if flood:      cmd.append("--flood")
    if no_cascade: cmd.append("--no-cascade")

    def _run_mc():
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        return subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(BASE_DIR), timeout=300,
            env=env, encoding="utf-8", errors="replace"
        )

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_mc_executor, _run_mc)
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "Monte Carlo timed out (5 min limit)")
    except Exception as e:
        raise HTTPException(500, f"Monte Carlo failed to start: {e}")

    if result.returncode != 0:
        err = result.stderr.strip()[:600] or result.stdout.strip()[:600]
        raise HTTPException(500, f"MC error: {err}")

    csv_path = DATA_DIR / "monte_carlo_results.csv"
    if not csv_path.exists():
        raise HTTPException(500, "Results CSV not found — check monte_carlo_3.py output")

    results = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            results.append(row)

    N = len(results)
    if N == 0:
        raise HTTPException(500, "Empty results file")

    def avg(key):
        vals = [float(r.get(key, 0)) for r in results]
        return round(sum(vals) / len(vals), 3)

    def prob(fn):
        return round(sum(1 for r in results if fn(r)) / N * 100, 1)

    from collections import Counter
    c_tx, c_pump, c_tower = Counter(), Counter(), Counter()
    for r in results:
        for nm in r.get("failed_transformers","").split("; "):
            if nm and nm != "none": c_tx[nm] += 1
        for nm in r.get("failed_pumps","").split("; "):
            if nm and nm != "none": c_pump[nm] += 1
        for nm in r.get("failed_towers","").split("; "):
            if nm and nm != "none": c_tower[nm] += 1

    return JSONResponse({
        "runs": N,
        "risk": {
            "Transformer failure":       prob(lambda r: float(r.get("power_tx_failed",0)) > 0),
            "Water pump failure":        prob(lambda r: float(r.get("water_pumps_failed",0)) > 0),
            "Pump on backup generator":  prob(lambda r: float(r.get("water_pumps_backup",0)) > 0),
            "Water tower draining":      prob(lambda r: float(r.get("water_towers_draining",0)) > 0),
            "Water tower empty":         prob(lambda r: float(r.get("water_towers_empty",0)) > 0),
            "Pipe burst":                prob(lambda r: float(r.get("water_pipe_bursts",0)) > 0),
            "Telecom tower on battery":  prob(lambda r: float(r.get("telecom_on_battery",0)) > 0),
            "Telecom tower failed":      prob(lambda r: float(r.get("telecom_failed",0)) > 0),
            "Cross-network cascade":     prob(lambda r: float(r.get("real_cross_network_cascade",0)) > 0),
            "Water pressure critical":   prob(lambda r: float(r.get("water_min_pressure",1)) < 0.4),
            "Telecom battery critical":  prob(lambda r: float(r.get("telecom_min_batt_pct",100)) < 30),
        },
        "averages": {
            "subs_failed":     avg("power_subs_failed"),
            "tx_failed":       avg("power_tx_failed"),
            "feeder_drops":    avg("power_feeder_drops"),
            "pumps_failed":    avg("water_pumps_failed"),
            "pumps_backup":    avg("water_pumps_backup"),
            "towers_draining": avg("water_towers_draining"),
            "avg_pressure":    avg("water_avg_pressure"),
            "min_pressure":    avg("water_min_pressure"),
            "telecom_battery": avg("telecom_on_battery"),
            "cascade_events":  avg("total_cascade_events"),
        },
        "vulnerable": {
            "transformers": [{"name": k, "pct": round(v/N*100,1)} for k,v in c_tx.most_common(5)],
            "pumps":        [{"name": k, "pct": round(v/N*100,1)} for k,v in c_pump.most_common(3)],
            "towers":       [{"name": k, "pct": round(v/N*100,1)} for k,v in c_tower.most_common(3)],
        }
    })


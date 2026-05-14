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
                        node_names[nid] = props.get("name") or nid

    await orch.inject_scenario("recovery", {"nodes": affected_nodes, "names": node_names})
    return {"status": "recovery_initiated", "affected_count": len(affected_nodes)}


# ── Manual Failure / Recovery ─────────────────────────────────────────────────

@app.post("/api/simulation/fail-node")
async def fail_node(body: dict):
    node_id = str(body.get("node_id", ""))
    print(f"Manual fail-node request received for: {node_id}")
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")

    # Find name and network from cache
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

    # 1. Directly broadcast initial failure for immediate UI feedback
    direct_event = {
        "event_id": str(uuid.uuid4())[:8],
        "timestamp": time.time(),
        "tick": orch.current_tick,
        "event_type": "NODE_FAILED",
        "source_network": source_network.value,
        "node_id": node_id,
        "node_name": display_name,
        "severity": 1.0,
        "affected_nodes": [],
        "cascade_depth": 0,
        "metadata": {"reason": "manual_injection"}
    }
    await manager.broadcast({"type": "event", "data": direct_event})

    # 2. Inject into agent-based simulation for cascading failures
    # Agents handle the dependency logic defined in their files.
    await orch.inject_scenario("flood", {"nodes": [node_id], "names": {node_id: display_name}})

    async def run_steps():
        # Run multiple ticks to allow cascades to propagate through agents
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

    # 1. Direct visual update
    direct_event = {
        "event_id": str(uuid.uuid4())[:8],
        "timestamp": time.time(),
        "tick": orch.current_tick,
        "event_type": "NODE_RECOVERED",
        "source_network": "system",
        "node_id": node_id,
        "node_name": None,
        "severity": 0.0,
        "affected_nodes": [],
        "cascade_depth": 0,
        "metadata": {"reason": "manual_recovery"}
    }
    await manager.broadcast({"type": "event", "data": direct_event})

    # 2. Inject recovery into simulation
    await orch.inject_scenario("recovery", {"nodes": [node_id], "names": {}})
    asyncio.create_task(orch.run_step())
    return {"status": "node_recovered", "node_id": node_id}

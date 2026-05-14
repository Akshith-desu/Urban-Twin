"""
Urban Twin - FastAPI Bridge Server
Exposes graph data, live WebSocket events, flood injection and Monte Carlo.
Run from src/: uvicorn api_server:app --reload --port 8000
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import geopandas as gpd
import json, os, random, asyncio, math, time
from typing import Dict, List, Optional, Set
from pathlib import Path
import numpy as np
from event_bus import EventBus
from event_schema import EventType
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

@app.websocket("/ws/events")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


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
                # Map by node_id (stringified)
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
                    # Merge JSON info into properties
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
    print("Urban Twin API starting…")
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
    
    # Start the simulation orchestrator
    await orch.start()
    print("Orchestrator started.")

    # Bridge EventBus to WebSocket Manager
    async def bridge_to_ws():
        print("WebSocket Bridge: Waiting for events...")
        async for event in bus.get_events("api_server_ws", [t for t in EventType]):
            print(f"WebSocket Bridge: Broadcasting {event.event_type} for {event.node_id}")
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
    return JSONResponse(_dep_cache)

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


# ── Monte Carlo ───────────────────────────────────────────────────────────────

@app.post("/api/simulation/montecarlo")
async def montecarlo(body: dict):
    n_runs   = min(int(body.get("n_runs", 200)), 500)
    scenario = body.get("scenario", "flood")
    target   = body.get("target_network", "all")

    networks = NETWORKS if target == "all" else [target]
    nodes = []
    for net in networks:
        for f in _graph_cache.get(net, {}).get("nodes", {}).get("features", []):
            p = f.get("properties", {}) or {}
            g = f.get("geometry", {})
            if g.get("type") == "Point":
                c = g["coordinates"]
                nodes.append({
                    "id": p.get("node_id", str(id(f))),
                    "name": p.get("name"),
                    "node_type": p.get("node_type"),
                    "lon": c[0], "lat": c[1],
                    "network": net,
                    "criticality": float(p.get("criticality") or 1.0),
                    "flood_risk": bool(p.get("flood_risk", False)),
                })

    if not nodes:
        return JSONResponse({"results": [], "total_runs": 0})

    lats = [n["lat"] for n in nodes]
    lons = [n["lon"] for n in nodes]
    clat = sum(lats) / len(lats)
    clon = sum(lons) / len(lons)
    lr   = max(lats) - min(lats)
    lonr = max(lons) - min(lons)

    counts = {n["id"]: 0 for n in nodes}
    for _ in range(n_runs):
        if scenario == "flood":
            flat = clat + random.uniform(-lr * 0.45, lr * 0.45)
            flon = clon + random.uniform(-lonr * 0.45, lonr * 0.45)
            frad = random.uniform(0.003, 0.009)
            for node in nodes:
                d = math.hypot(node["lat"] - flat, node["lon"] - flon)
                if d < frad:
                    prob = 1.0 - d / frad * 0.5
                    if node["flood_risk"]:
                        prob = min(1.0, prob * 1.3)
                    if random.random() < prob:
                        counts[node["id"]] += 1
        else:
            for node in random.sample(nodes, max(1, len(nodes) // 10)):
                counts[node["id"]] += 1

    results = []
    for node in nodes:
        prob = counts[node["id"]] / n_runs
        if prob > 0.01:
            results.append({
                "node_id": node["id"], "network": node["network"],
                "node_type": node.get("node_type"),
                "name": node["name"],
                "lat": node["lat"], "lon": node["lon"],
                "failure_probability": round(prob, 4),
                "criticality": node["criticality"],
            })
    results.sort(key=lambda x: x["failure_probability"], reverse=True)
    return JSONResponse({"results": results, "total_runs": n_runs,
                         "scenario": scenario, "most_vulnerable": results[:10]})


# ── Flood Injection ───────────────────────────────────────────────────────────

@app.post("/api/simulation/flood")
async def inject_flood(body: dict):
    lat = float(body.get("lat", 12.9762))
    lon = float(body.get("lon", 77.6265))
    radius_m = float(body.get("radius_m", 500))
    
    # Identify nodes in the flood zone
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
                        # Construct name similarly to agent
                        n_type = str(props.get("node_type", "Node")).replace("_", " ").title()
                        n_name = props.get("name")
                        display_name = f"{n_type} {n_name}" if n_name and str(n_name) != str(nid) else f"{n_type} ({nid})"
                        node_names[nid] = display_name

    print(f"Injecting flood at {lat}, {lon} with radius {radius_m}m. Affected nodes: {len(affected_nodes)}")
    # Inject into orchestrator
    asyncio.create_task(orch.inject_scenario("flood", {"nodes": affected_nodes, "names": node_names}))
    
    # Also run a few steps automatically
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

    # Inject into orchestrator
    asyncio.create_task(orch.inject_scenario("recovery", {"nodes": affected_nodes, "names": node_names}))
    
    return {"status": "recovery_initiated", "affected_count": len(affected_nodes)}




@app.get("/api/export/kepler")
async def export_kepler():
    # Combine all networks and dependencies into a Kepler-compatible JSON
    datasets = []
    
    # 1. Network Nodes & Edges
    features = []
    for net in NETWORKS:
        d = _graph_cache.get(net, {})
        for f in d.get("nodes", {}).get("features", []):
            f["properties"]["_network"] = net
            features.append(f)
        for f in d.get("edges", {}).get("features", []):
            f["properties"]["_network"] = net
            features.append(f)
            
    datasets.append({
        "info": {"id": "infrastructure", "label": "Urban Infrastructure"},
        "data": {
            "type": "FeatureCollection",
            "features": features
        }
    })
    
    # 2. Dependencies as Arcs
    dep_features = []
    for dep in _dep_cache:
        # Find coordinates
        from_coords = None
        to_coords = None
        for net in NETWORKS:
            for f in _graph_cache[net]["nodes"]["features"]:
                if f["properties"].get("node_id") == dep["from_node"]:
                    from_coords = f["geometry"]["coordinates"]
                if f["properties"].get("node_id") == dep["to_node"]:
                    to_coords = f["geometry"]["coordinates"]
        
        if from_coords and to_coords:
            dep_features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [from_coords, to_coords]
                },
                "properties": dep
            })
            
    datasets.append({
        "info": {"id": "dependencies", "label": "Inter-dependencies"},
        "data": {
            "type": "FeatureCollection",
            "features": dep_features
        }
    })

    return JSONResponse({
        "version": "v1",
        "config": {
            "visState": {
                "layers": [
                    {
                        "id": "infra-nodes",
                        "type": "geojson",
                        "config": {
                            "dataId": "infrastructure",
                            "label": "Infrastructure Nodes",
                            "color": [18, 147, 154],
                            "columns": {"geojson": "geometry"},
                            "isVisible": True,
                            "visConfig": {"radius": 10, "opacity": 0.8}
                        }
                    },
                    {
                        "id": "dep-arcs",
                        "type": "arc",
                        "config": {
                            "dataId": "dependencies",
                            "label": "Dependency Arcs",
                            "color": [255, 153, 31],
                            "columns": {"geojson": "geometry"},
                            "isVisible": True,
                            "visConfig": {"thickness": 2, "opacity": 0.6}
                        }
                    }
                ]
            }
        },
        "datasets": datasets
    })


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()   # keep-alive ping
    except WebSocketDisconnect:
        manager.disconnect(ws)

@app.post("/api/simulation/fail-node")
async def fail_node(body: dict):
    node_id = str(body.get("node_id", ""))
    print(f"Manual fail-node request received for: {node_id}")
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")
    
    # Try to find name in cache
    display_name = "Asset " + str(node_id)
    for net in NETWORKS:
        for f in _graph_cache.get(net, {}).get("nodes", {}).get("features", []):
            props = f.get("properties", {})
            if str(props.get("node_id")) == str(node_id):
                n_type = str(props.get("node_type", "Node")).replace("_", " ").title()
                n_name = props.get("name")
                display_name = f"{n_type} {n_name}" if n_name and str(n_name) != str(node_id) else f"{n_type} ({node_id})"
                break
    
    await orch.inject_scenario("flood", {"nodes": [node_id], "names": {node_id: display_name}})
    
    # Step simulation several times to propagate effects
    async def run_multiple_steps():
        for _ in range(5):
            await orch.run_step()
            await asyncio.sleep(0.5)
            
    asyncio.create_task(run_multiple_steps())
    
    return {"status": "node_failed", "node_id": node_id, "name": display_name}

@app.post("/api/simulation/recover-node")
async def recover_node(body: dict):
    node_id = str(body.get("node_id", ""))
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")
        
    await orch.inject_scenario("recovery", {"nodes": [node_id], "names": {}})
    asyncio.create_task(orch.run_step())
    
    return {"status": "node_recovered", "node_id": node_id}

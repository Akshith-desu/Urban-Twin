"""
water_network.py
Builds the water distribution infrastructure graph for Halasuru, Bengaluru.

Steps:
  1. Query OSMnx for water towers and pump stations; fall back to synthetic if sparse
  2. Generate synthetic pipe junctions along road skeleton (BWSSB pipe data not public)
  3. Assign node attributes (physics values from ranges below, Monte Carlo ready)
  4. Build directed pipe edges: pump_station → junction → junction → water_tower topology
  5. Connect buildings → nearest pipe junction (mirrors power.py building→transformer pattern)
  6. Save water_nodes.gpkg, water_edges.gpkg, water.json
  7. Display using matplotlib / geopandas

Node types:
  pump_station  — pressurised source nodes (powered by grid, subject to Equation 1)
  water_tower   — gravity-head buffer nodes (subject to Equations 4 + 5)
  pipe_junction — intermediate distribution nodes (subject to Equations 2, 3, 6)

Cascade equations embedded at bottom (comments) for agent reference.
"""

import os
import json
import random
import math
import osmnx as ox
import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import networkx as nx

from shapely.geometry import LineString, Point
from scipy.spatial   import cKDTree
from pyproj          import Transformer


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION — all hardcoded values and ranges live here only
# ══════════════════════════════════════════════════════════════════════════════

CENTER          = (12.9762, 77.6265)    # Halasuru, Bengaluru — matches power.py
RADIUS_M        = 2000
CRS             = "EPSG:32643"          # UTM zone 43N — metres
OUTPUT_DIR      = "graphs"
DATA_DIR        = "data"
ROAD_GRAPH_PATH = f"{OUTPUT_DIR}/road_graph.graphml"
BUILDINGS_PATH  = f"{DATA_DIR}/buildings.gpkg"          # same source as power.py

# ── OSM query tags ────────────────────────────────────────────────────────────
WATER_TOWER_TAGS   = {"man_made": "water_tower"}
PUMP_STATION_TAGS  = {"man_made": ["pumping_station", "water_works"]}

# Minimum synthetic node counts if OSM returns fewer than these
MIN_PUMP_STATIONS  = 5
MIN_WATER_TOWERS   = 4

# ── pump station synthetic attribute ranges ───────────────────────────────────
PUMP_INITIAL_PRESSURE_RANGE     = (0.85, 1.0)
PUMP_BACKUP_GEN_RUNTIME_H_RANGE = (1.0, 4.0)
PUMP_STORAGE_CAPACITY_M3_RANGE  = (3000, 7000)
PUMP_PIPE_AGE_YEARS_RANGE       = (5, 15)
PUMP_BASE_FLOW_RATE_RANGE       = (0.6, 1.0)
PUMP_PIPE_MATERIAL_OPTIONS      = ["PVC", "DI", "CI"]
PUMP_PIPE_DIAMETER_MM_RANGE     = (200, 400)

# ── water tower synthetic attribute ranges ────────────────────────────────────
TOWER_HEIGHT_M_RANGE            = (15.0, 30.0)
TOWER_WATER_LEVEL_RANGE         = (0.55, 0.95)
TOWER_STORAGE_CAPACITY_M3_RANGE = (500, 2000)
TOWER_PIPE_AGE_YEARS_RANGE      = (5, 15)
TOWER_PIPE_MATERIAL_OPTIONS     = ["PVC", "DI", "CI"]
TOWER_PIPE_DIAMETER_MM_RANGE    = (100, 200)

# ── pipe junction synthetic attribute ranges ──────────────────────────────────
JUNCTION_PIPE_AGE_YEARS_RANGE   = (8, 30)
JUNCTION_INITIAL_PRESSURE_RANGE = (0.5, 0.9)
JUNCTION_PIPE_MATERIAL_OPTIONS  = ["PVC", "DI", "CI"]
JUNCTION_PIPE_DIAMETER_MM_RANGE = (100, 200)

# ── pipe edge attribute ranges ────────────────────────────────────────────────
PIPE_C_NEW_BY_MATERIAL          = {"PVC": 150, "DI": 140, "CI": 100}
PIPE_BASE_FLOW_RANGE            = (0.3, 0.8)

# ── building service connection ranges (mirrors power.py LV ranges) ───────────
BUILDING_PIPE_DIAMETER_MM_RANGE = (25, 63)      # domestic service pipe: 25–63mm
BUILDING_PIPE_AGE_YEARS_RANGE   = (5, 40)       # older domestic connections

# ── cascade thresholds ────────────────────────────────────────────────────────
LOW_PRESSURE_THRESHOLD          = 0.40
CRITICAL_PRESSURE               = 0.20
TOWER_DRAIN_THRESHOLD           = 0.20
HEALTH_FAIL_THRESHOLD           = 0.30
PRESSURE_DECAY_K                = 0.085
TICKS_LOW_PRESSURE_FOR_DAMAGE   = 3

# ── display colours ───────────────────────────────────────────────────────────
NODE_COLOURS = {
    "pump_station":  "#3b82f6",
    "water_tower":   "#06b6d4",
    "pipe_junction": "#a1a1aa",
}
EDGE_COLOURS = {
    "main_supply":           "#1d4ed8",   # dark blue  — pump → junction
    "tower_feed":            "#0891b2",   # teal       — junction → tower
    "junction_link":         "#d4d4d8",   # light gray — junction → junction
    "building_service_drop": "#7dd3fc",   # sky blue   — building stub
    "building_road_feeder":  "#38bdf8",   # light blue — building → junction
}

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(DATA_DIR,   exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def ll_to_proj(lon, lat, crs_transformer):
    return crs_transformer.transform(lon, lat)


def road_path_geometry(G_road, road_node_u, road_node_v):
    """Shortest path along road graph → (LineString, length_m)."""
    try:
        path = nx.shortest_path(G_road, road_node_u, road_node_v, weight="length")
        coords = [(G_road.nodes[n]["x"], G_road.nodes[n]["y"]) for n in path]
        if len(coords) < 2:
            return None, 0
        length = sum(
            G_road.edges[path[i], path[i + 1], 0].get("length", 0)
            for i in range(len(path) - 1)
        )
        return LineString(coords), length
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None, None


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — QUERY OSM + SYNTHETIC FALLBACK
# ══════════════════════════════════════════════════════════════════════════════

def query_or_synthesise_pump_stations(crs_transformer):
    print("  Querying OSM for pump stations...")
    results = []
    try:
        features = ox.features_from_point(CENTER, tags=PUMP_STATION_TAGS, dist=RADIUS_M)
        features = features.to_crs(CRS)
        features["geometry"] = features.geometry.apply(
            lambda g: g.centroid if g.geom_type in ["Polygon", "MultiPolygon"] else g
        )
        for i, (_, row) in enumerate(features.iterrows()):
            results.append({
                "id": f"WPS-OSM-{i+1:02d}", "x": row.geometry.x, "y": row.geometry.y,
                "name": str(row.get("name", f"Pump Station {i+1}")), "source": "osm",
            })
        print(f"    OSM pump stations found: {len(results)}")
    except Exception as e:
        print(f"    OSM pump station query failed ({e}), using synthetic only")

    synthetic_pumps = [
        {"lon": 77.6265, "lat": 12.9762, "name": "Halasuru Main Pump Station"},
        {"lon": 77.6390, "lat": 12.9870, "name": "North Zone Pump Station"},
        {"lon": 77.6420, "lat": 12.9700, "name": "South Zone Pump Station"},
        {"lon": 77.6500, "lat": 12.9790, "name": "East Zone Pump Station"},
        {"lon": 77.6200, "lat": 12.9760, "name": "Ulsoor Zone Pump Station"},
        {"lon": 77.6320, "lat": 12.9630, "name": "Domlur Pump Station"},
    ]
    if len(results) < MIN_PUMP_STATIONS:
        needed = MIN_PUMP_STATIONS - len(results)
        for i, sp in enumerate(synthetic_pumps[:needed]):
            x, y = ll_to_proj(sp["lon"], sp["lat"], crs_transformer)
            results.append({"id": f"WPS-SYN-{i+1:02d}", "x": x, "y": y,
                            "name": sp["name"], "source": "synthetic"})
        print(f"    Added {needed} synthetic pump stations (total: {len(results)})")
    return results


def query_or_synthesise_water_towers(crs_transformer):
    print("  Querying OSM for water towers...")
    results = []
    try:
        features = ox.features_from_point(CENTER, tags=WATER_TOWER_TAGS, dist=RADIUS_M)
        features = features.to_crs(CRS)
        features["geometry"] = features.geometry.apply(
            lambda g: g.centroid if g.geom_type in ["Polygon", "MultiPolygon"] else g
        )
        for i, (_, row) in enumerate(features.iterrows()):
            results.append({
                "id": f"WWT-OSM-{i+1:02d}", "x": row.geometry.x, "y": row.geometry.y,
                "name": str(row.get("name", f"Water Tower {i+1}")), "source": "osm",
            })
        print(f"    OSM water towers found: {len(results)}")
    except Exception as e:
        print(f"    OSM water tower query failed ({e}), using synthetic only")

    synthetic_towers = [
        {"lon": 77.6310, "lat": 12.9840, "name": "Indiranagar Elevated Reservoir"},
        {"lon": 77.6450, "lat": 12.9750, "name": "HAL Layout Overhead Tank"},
        {"lon": 77.6180, "lat": 12.9800, "name": "Ulsoor Elevated Tank"},
        {"lon": 77.6380, "lat": 12.9660, "name": "Domlur Overhead Reservoir"},
        {"lon": 77.6280, "lat": 12.9720, "name": "Halasuru North Tank"},
    ]
    if len(results) < MIN_WATER_TOWERS:
        needed = MIN_WATER_TOWERS - len(results)
        for i, st in enumerate(synthetic_towers[:needed]):
            x, y = ll_to_proj(st["lon"], st["lat"], crs_transformer)
            results.append({"id": f"WWT-SYN-{i+1:02d}", "x": x, "y": y,
                            "name": st["name"], "source": "synthetic"})
        print(f"    Added {needed} synthetic water towers (total: {len(results)})")
    return results


def generate_pipe_junctions(G_road, pump_stations, water_towers):
    """
    Pipe junctions at degree>=4 road intersections, spatially thinned to 600m.
    ~35-40 junctions for a 2km-radius area — realistic BWSSB zone density.
    """
    MIN_JUNCTION_SPACING_M = 600

    print("  Generating pipe junctions at major road intersections (degree >= 4)...")

    existing_coords = np.array([[n["x"], n["y"]] for n in pump_stations + water_towers])
    existing_tree = cKDTree(existing_coords) if len(existing_coords) > 0 else None

    candidates = []
    for road_nid, road_data in G_road.nodes(data=True):
        if G_road.degree(road_nid) < 4:
            continue
        if existing_tree is not None:
            dist, _ = existing_tree.query([road_data["x"], road_data["y"]], k=1)
            if dist < 200:
                continue
        candidates.append((road_nid, road_data["x"], road_data["y"]))

    print(f"    Degree>=4 candidates: {len(candidates)}")

    kept = []
    kept_coords = []
    for road_nid, cx, cy in candidates:
        if len(kept_coords) == 0:
            kept.append((road_nid, cx, cy))
            kept_coords.append([cx, cy])
            continue
        dist, _ = cKDTree(np.array(kept_coords)).query([cx, cy], k=1)
        if dist > MIN_JUNCTION_SPACING_M:
            kept.append((road_nid, cx, cy))
            kept_coords.append([cx, cy])

    junctions = [
        {"id": f"WPJ-{i+1:04d}", "x": cx, "y": cy, "road_node_ref": road_nid}
        for i, (road_nid, cx, cy) in enumerate(kept)
    ]
    print(f"    After spatial thinning: {len(junctions)} pipe junctions")
    return junctions


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — BUILD NODE ATTRIBUTES
# ══════════════════════════════════════════════════════════════════════════════

def build_pump_station_attributes(pump_stations):
    nodes = []
    for ps in pump_stations:
        pipe_material = random.choice(PUMP_PIPE_MATERIAL_OPTIONS)
        pipe_age      = random.uniform(*PUMP_PIPE_AGE_YEARS_RANGE)
        c_new         = PIPE_C_NEW_BY_MATERIAL[pipe_material]
        c_eff         = max(60.0, c_new - 1.0 * pipe_age)
        nodes.append({
            "node_id": ps["id"], "name": ps["name"], "node_type": "pump_station",
            "source": ps["source"], "x": ps["x"], "y": ps["y"],
            "pipe_material": pipe_material,
            "pipe_diameter_mm": random.randint(*PUMP_PIPE_DIAMETER_MM_RANGE),
            "fill_source_node": None,
            "pipe_age_years": round(pipe_age, 1),
            "backup_gen_runtime_h": round(random.uniform(*PUMP_BACKUP_GEN_RUNTIME_H_RANGE), 2),
            "initial_pressure": round(random.uniform(*PUMP_INITIAL_PRESSURE_RANGE), 3),
            "storage_capacity_m3": round(random.uniform(*PUMP_STORAGE_CAPACITY_M3_RANGE), 1),
            "c_new": c_new, "c_eff": round(c_eff, 2),
            "health": 1.0, "health_history": [1.0]*5,
            "pressure": round(random.uniform(*PUMP_INITIAL_PRESSURE_RANGE), 3),
            "flow_rate": round(random.uniform(*PUMP_BASE_FLOW_RATE_RANGE), 3),
            "water_level": None, "has_backup_gen": True,
            "backup_gen_remaining_h": round(random.uniform(*PUMP_BACKUP_GEN_RUNTIME_H_RANGE), 2),
            "on_grid_power": True, "ticks_low_pressure": 0,
            "operational_status": "normal",
            "criticality": 9.0, "pop_density": 1.0,
            "flood_risk": False, "near_critical_facility": False,
            "power_dependency": None, "operator": "BWSSB",
        })
    return nodes


def build_water_tower_attributes(water_towers):
    nodes = []
    for wt in water_towers:
        pipe_material  = random.choice(TOWER_PIPE_MATERIAL_OPTIONS)
        pipe_age       = random.uniform(*TOWER_PIPE_AGE_YEARS_RANGE)
        c_new          = PIPE_C_NEW_BY_MATERIAL[pipe_material]
        c_eff          = max(60.0, c_new - 1.0 * pipe_age)
        tower_height_m = round(random.uniform(*TOWER_HEIGHT_M_RANGE), 1)
        water_level    = round(random.uniform(*TOWER_WATER_LEVEL_RANGE), 3)
        hh_pressure    = round(water_level, 3)
        nodes.append({
            "node_id": wt["id"], "name": wt["name"], "node_type": "water_tower",
            "source": wt["source"], "x": wt["x"], "y": wt["y"],
            "pipe_material": pipe_material,
            "pipe_diameter_mm": random.randint(*TOWER_PIPE_DIAMETER_MM_RANGE),
            "tower_height_m": tower_height_m,
            "pipe_age_years": round(pipe_age, 1),
            "water_level": water_level,
            "storage_capacity_m3": round(random.uniform(*TOWER_STORAGE_CAPACITY_M3_RANGE), 1),
            "fill_source_node": None,
            "c_new": c_new, "c_eff": round(c_eff, 2),
            "reference_head": tower_height_m,
            "hydraulic_head_pressure": hh_pressure,
            "health": 1.0, "health_history": [1.0]*5,
            "pressure": hh_pressure,
            "flow_rate": round(random.uniform(0.4, 0.8), 3),
            "has_backup_gen": False, "on_grid_power": False,
            "ticks_low_pressure": 0, "operational_status": "normal",
            "is_draining": False,
            "criticality": 7.0, "pop_density": 1.0,
            "flood_risk": False, "near_critical_facility": False,
            "power_dependency": None, "operator": "BWSSB",
        })
    return nodes


def build_pipe_junction_attributes(junctions):
    nodes = []
    for jn in junctions:
        pipe_material = random.choice(JUNCTION_PIPE_MATERIAL_OPTIONS)
        pipe_age      = random.uniform(*JUNCTION_PIPE_AGE_YEARS_RANGE)
        c_new         = PIPE_C_NEW_BY_MATERIAL[pipe_material]
        c_eff         = max(60.0, c_new - 1.0 * pipe_age)
        init_pressure = round(random.uniform(*JUNCTION_INITIAL_PRESSURE_RANGE), 3)
        nodes.append({
            "node_id": jn["id"], "name": jn["id"], "node_type": "pipe_junction",
            "source": "synthetic", "x": jn["x"], "y": jn["y"],
            "road_node_ref": jn.get("road_node_ref"),
            "pipe_material": pipe_material,
            "pipe_diameter_mm": random.randint(*JUNCTION_PIPE_DIAMETER_MM_RANGE),
            "fill_source_node": None, "tower_height_m": None,
            "pipe_age_years": round(pipe_age, 1),
            "initial_pressure": init_pressure,
            "c_new": c_new, "c_eff": round(c_eff, 2),
            "health": 1.0, "health_history": [1.0]*5,
            "pressure": init_pressure,
            "flow_rate": round(random.uniform(0.3, 0.7), 3),
            "water_level": None, "has_backup_gen": False,
            "on_grid_power": False, "ticks_low_pressure": 0,
            "burst_occurred": False, "operational_status": "normal",
            "criticality": 1.0, "pop_density": 1.0,
            "flood_risk": False, "near_critical_facility": False,
            "power_dependency": None, "operator": "BWSSB",
        })
    return nodes


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — BUILD DIRECTED PIPE EDGES (infrastructure backbone)
# ══════════════════════════════════════════════════════════════════════════════

def make_pipe_edge(edge_counter, from_id, to_id, edge_type,
                   from_node_data, to_node_data, G_road):
    """Single edge factory — road-routed geometry + Hazen-Williams physics."""
    fx, fy = from_node_data["x"], from_node_data["y"]
    tx, ty = to_node_data["x"],   to_node_data["y"]

    snap_f = ox.nearest_nodes(G_road, fx, fy)
    snap_t = ox.nearest_nodes(G_road, tx, ty)
    geom, length = road_path_geometry(G_road, snap_f, snap_t)
    if geom is None:
        geom   = LineString([(fx, fy), (tx, ty)])
        length = math.sqrt((tx - fx)**2 + (ty - fy)**2)

    pipe_material = from_node_data.get("pipe_material", "DI")
    pipe_age      = from_node_data.get("pipe_age_years", 15.0)
    pipe_diameter = from_node_data.get("pipe_diameter_mm", 150)
    c_new         = PIPE_C_NEW_BY_MATERIAL.get(pipe_material, 140)
    c_eff         = max(60.0, c_new - 1.0 * pipe_age)
    length_m      = float(length)
    loss_fraction = round(
        6.82e-4 * length_m / (pipe_diameter ** 1.165) * (140.0 / c_eff), 6
    )
    return {
        "edge_id": edge_counter, "from_node": from_id, "to_node": to_id,
        "edge_type": edge_type,
        "pipe_length_m": round(length_m, 2), "pipe_diameter_mm": pipe_diameter,
        "pipe_material": pipe_material, "pipe_age_years": round(pipe_age, 1),
        "c_new": c_new, "c_eff": round(c_eff, 2), "loss_fraction": loss_fraction,
        "base_flow": round(random.uniform(*PIPE_BASE_FLOW_RANGE), 3),
        "health": 1.0, "blocked": False, "burst": False, "geometry": geom,
    }


def build_pipe_edges(pump_nodes, tower_nodes, junction_nodes, G_road):
    """
    Build the water infrastructure backbone:

    1. junction_link : junction ↔ junction (K=3 neighbours, bidirectional)
    2. main_supply   : pump → 3 nearest junctions per pump
    3. MST pass      : any junction component with no pump gets bridged to its
                       nearest pump — guarantees EVERY junction is reachable
    4. tower_feed    : nearest junction → each water tower

    The MST pass is the key fix: it finds disconnected components in the
    junction+pump graph and adds a direct main_supply edge from the nearest
    pump to the closest junction in that component.
    """
    print("  Building directed pipe edges...")

    all_nodes   = pump_nodes + tower_nodes + junction_nodes
    node_lookup = {n["node_id"]: n for n in all_nodes}

    pump_ids    = [n["node_id"] for n in pump_nodes]
    pump_coords = np.array([[n["x"], n["y"]] for n in pump_nodes])
    tree_pumps  = cKDTree(pump_coords) if len(pump_coords) > 0 else None

    junction_ids    = [n["node_id"] for n in junction_nodes]
    junction_coords = (np.array([[n["x"], n["y"]] for n in junction_nodes])
                       if junction_nodes else np.array([]))
    tree_junctions  = cKDTree(junction_coords) if len(junction_coords) > 0 else None

    edges        = []
    edge_counter = 1

    def add_edge(from_id, to_id, edge_type):
        nonlocal edge_counter
        e = make_pipe_edge(edge_counter, from_id, to_id, edge_type,
                           node_lookup[from_id], node_lookup[to_id], G_road)
        edges.append(e)
        edge_counter += 1

    # ── 1. junction_link : each junction → K=3 nearest neighbours ────────────
    if junction_nodes:
        JUNCTION_LINK_K = 3
        seen_pairs = set()
        for jn in junction_nodes:
            k = min(JUNCTION_LINK_K + 1, len(junction_ids))
            _, idxs = tree_junctions.query([jn["x"], jn["y"]], k=k)
            idx_list = list(idxs[1:]) if hasattr(idxs, "__len__") and len(idxs) > 1 else []
            for ni in idx_list:
                neighbour_id = junction_ids[int(ni)]
                pair = tuple(sorted([jn["node_id"], neighbour_id]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                add_edge(jn["node_id"], neighbour_id, "junction_link")
                add_edge(neighbour_id, jn["node_id"], "junction_link")

        jl_count = sum(1 for e in edges if e["edge_type"] == "junction_link")
        print(f"    Junction-link edges added: {jl_count}")

    # ── 2. main_supply : pump → 3 nearest junctions per pump ─────────────────
    if tree_junctions is not None:
        for pump in pump_nodes:
            k = min(3, len(junction_ids))
            _, idxs = tree_junctions.query([pump["x"], pump["y"]], k=k)
            idx_list = [idxs] if not hasattr(idxs, "__len__") else list(idxs)
            for ji in idx_list:
                junc_id = junction_ids[int(ji)]
                add_edge(pump["node_id"], junc_id, "main_supply")

    # ── 3. MST connectivity pass — bridge any junction island to a pump ───────
    if junction_nodes and pump_nodes:
        G_conn = nx.Graph()
        G_conn.add_nodes_from(junction_ids)
        G_conn.add_nodes_from(pump_ids)
        for e in edges:
            if e["edge_type"] in ("junction_link", "main_supply"):
                G_conn.add_edge(e["from_node"], e["to_node"])

        pump_set = set(pump_ids)
        bridges_added = 0
        for component in nx.connected_components(G_conn):
            if component & pump_set:
                continue   # already connected to a pump
            comp_junctions = [j for j in component if j in set(junction_ids)]
            if not comp_junctions:
                continue
            # find the junction in this island closest to any pump
            best_junc, best_pump, best_dist = None, None, float("inf")
            for jid in comp_junctions:
                jx, jy = node_lookup[jid]["x"], node_lookup[jid]["y"]
                _, pi  = tree_pumps.query([jx, jy], k=1)
                px, py = pump_nodes[int(pi)]["x"], pump_nodes[int(pi)]["y"]
                d = math.sqrt((jx - px)**2 + (jy - py)**2)
                if d < best_dist:
                    best_dist = d
                    best_junc = jid
                    best_pump = pump_ids[int(pi)]
            add_edge(best_pump, best_junc, "main_supply")
            bridges_added += 1

        ms_count = sum(1 for e in edges if e["edge_type"] == "main_supply")
        print(f"    Main-supply edges (after MST pass, {bridges_added} bridges added): {ms_count}")

    # ── 4. tower_feed : nearest junction → each water tower ──────────────────
    if tree_junctions is not None and tree_pumps is not None:
        for tower in tower_nodes:
            _, ji = tree_junctions.query([tower["x"], tower["y"]], k=1)
            nearest_junc_id = junction_ids[int(ji)]
            _, pi = tree_pumps.query([tower["x"], tower["y"]], k=1)
            tower["fill_source_node"] = pump_ids[int(pi)]
            add_edge(nearest_junc_id, tower["node_id"], "tower_feed")

        tf_count = sum(1 for e in edges if e["edge_type"] == "tower_feed")
        print(f"    Tower-feed edges: {tf_count}")

    print(f"  Total infrastructure edges: {len(edges)}")
    return edges


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — BUILDING → NEAREST JUNCTION EDGES  (mirrors power.py exactly)
# ══════════════════════════════════════════════════════════════════════════════

def build_building_junction_edges(buildings, junction_nodes, G_road, edge_counter_start):
    """
    For each building — identical pattern to power.py build_building_transformer_edges:
      building_service_drop : building centroid → nearest road node (stub)
      building_road_feeder  : nearest road node → nearest junction road node (road-routed)

    Nearest junction plays the same role as nearest transformer in power.py.
    """
    print("Building → junction edges (road-routed)...")

    if not junction_nodes:
        print("  No junctions available — skipping building edges.")
        return gpd.GeoDataFrame([], crs=CRS), edge_counter_start

    junction_coords = np.array([[n["x"], n["y"]] for n in junction_nodes])
    junction_ids    = [n["node_id"] for n in junction_nodes]
    tree            = cKDTree(junction_coords)

    edges        = []
    edge_counter = edge_counter_start
    total        = len(buildings)

    for i, (building_idx, building) in enumerate(buildings.iterrows()):
        if i % 200 == 0:
            print(f"  Processing building {i+1}/{total}...")

        centroid = building.geometry.centroid
        bx, by   = centroid.x, centroid.y

        _, ji       = tree.query([bx, by], k=1)
        junction_id = junction_ids[int(ji)]
        jx          = junction_coords[int(ji)][0]
        jy          = junction_coords[int(ji)][1]

        snap_b  = ox.nearest_nodes(G_road, bx, by)
        snap_j  = ox.nearest_nodes(G_road, jx, jy)
        road_bx = G_road.nodes[snap_b]["x"]
        road_by = G_road.nodes[snap_b]["y"]
        road_jx = G_road.nodes[snap_j]["x"]
        road_jy = G_road.nodes[snap_j]["y"]

        pipe_age      = round(random.uniform(*BUILDING_PIPE_AGE_YEARS_RANGE), 1)
        pipe_diameter = random.randint(*BUILDING_PIPE_DIAMETER_MM_RANGE)
        pipe_material = random.choice(["PVC", "CI"])

        # service drop — building centroid → nearest road node
        service_geom = LineString([(bx, by), (road_bx, road_by)])
        edges.append({
            "edge_id":          edge_counter,
            "from_node":        f"Building_{building_idx}",
            "to_node":          junction_id,
            "edge_type":        "building_service_drop",
            "pipe_length_m":    round(float(service_geom.length), 2),
            "pipe_diameter_mm": pipe_diameter,
            "pipe_material":    pipe_material,
            "pipe_age_years":   pipe_age,
            "health":           1.0,
            "blocked":          False,
            "burst":            False,
            "geometry":         service_geom,
        })
        edge_counter += 1

        # road feeder — nearest road node → junction road node (road-routed)
        feeder_geom, feeder_length = road_path_geometry(G_road, snap_b, snap_j)
        if feeder_geom is None:
            feeder_geom   = LineString([(road_bx, road_by), (road_jx, road_jy)])
            feeder_length = feeder_geom.length

        edges.append({
            "edge_id":          edge_counter,
            "from_node":        f"Building_{building_idx}",
            "to_node":          junction_id,
            "edge_type":        "building_road_feeder",
            "pipe_length_m":    round(float(feeder_length), 2),
            "pipe_diameter_mm": pipe_diameter,
            "pipe_material":    pipe_material,
            "pipe_age_years":   pipe_age,
            "health":           1.0,
            "blocked":          False,
            "burst":            False,
            "geometry":         feeder_geom,
        })
        edge_counter += 1

    print(f"  Building edges added: {len(edges)} ({total} buildings × 2)")
    return gpd.GeoDataFrame(edges, crs=CRS), edge_counter


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — EXPORT JSON
# ══════════════════════════════════════════════════════════════════════════════

def export_water_json(nodes_list, infra_edges, building_edges_gdf, output_path):
    print("Exporting water.json...")

    node_records = []
    for n in nodes_list:
        record = {k: v for k, v in n.items()
                  if k not in ("geometry",) and not isinstance(v, list)}
        record["health_history"] = n.get("health_history", [1.0]*5)
        node_records.append(record)

    edge_records = []
    for e in infra_edges:
        rec = {k: v for k, v in e.items() if k != "geometry"}
        edge_records.append(rec)

    if building_edges_gdf is not None and len(building_edges_gdf) > 0:
        for _, row in building_edges_gdf.iterrows():
            rec = {k: v for k, v in row.items() if k != "geometry"}
            edge_records.append(rec)

    with open(output_path, "w") as f:
        json.dump({"nodes": node_records, "edges": edge_records}, f, indent=2)
    print(f"  Saved: {output_path}")


# ══════════════════════════════════════════════════════════════════════════════
# EXECUTION
# ══════════════════════════════════════════════════════════════════════════════

print("Loading road graph...")
G_road = ox.load_graphml(ROAD_GRAPH_PATH)
print(f"  Nodes: {G_road.number_of_nodes()}, Edges: {G_road.number_of_edges()}")

print("Loading buildings...")
buildings = gpd.read_file(BUILDINGS_PATH).to_crs(CRS)
print(f"  Buildings loaded: {len(buildings)}")

crs_transformer = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)

# Step 1 — query OSM + synthetic fallback
print("\n=== STEP 1: Query OSM / synthetic nodes ===")
raw_pumps     = query_or_synthesise_pump_stations(crs_transformer)
raw_towers    = query_or_synthesise_water_towers(crs_transformer)
raw_junctions = generate_pipe_junctions(G_road, raw_pumps, raw_towers)

# Step 2 — build node attributes
print("\n=== STEP 2: Build node attributes ===")
pump_nodes     = build_pump_station_attributes(raw_pumps)
tower_nodes    = build_water_tower_attributes(raw_towers)
junction_nodes = build_pipe_junction_attributes(raw_junctions)

all_nodes = pump_nodes + tower_nodes + junction_nodes
print(f"  Pump stations : {len(pump_nodes)}")
print(f"  Water towers  : {len(tower_nodes)}")
print(f"  Pipe junctions: {len(junction_nodes)}")
print(f"  Total nodes   : {len(all_nodes)}")

# Step 3 — infrastructure backbone edges
print("\n=== STEP 3: Build pipe edges ===")
infra_edges = build_pipe_edges(pump_nodes, tower_nodes, junction_nodes, G_road)

# Step 4 — building → junction edges (mirrors power.py)
print("\n=== STEP 4: Build building → junction edges ===")
next_counter = max(e["edge_id"] for e in infra_edges) + 1 if infra_edges else 1
building_edges_gdf, _ = build_building_junction_edges(
    buildings, junction_nodes, G_road, next_counter
)

# Step 5 — save GeoPackages
print("\n=== STEP 5: Save GeoPackages ===")
node_records_gdf = []
for n in all_nodes:
    rec = {k: v for k, v in n.items()
           if isinstance(v, (str, int, float, bool)) or v is None}
    rec["geometry"] = Point(n["x"], n["y"])
    node_records_gdf.append(rec)

nodes_gdf = gpd.GeoDataFrame(node_records_gdf, crs=CRS)
nodes_gdf.to_file(f"{OUTPUT_DIR}/water_nodes.gpkg", driver="GPKG")
print(f"  Saved: {OUTPUT_DIR}/water_nodes.gpkg")

infra_records_gdf = []
for e in infra_edges:
    rec = {k: v for k, v in e.items()
           if isinstance(v, (str, int, float, bool)) or v is None}
    rec["geometry"] = e["geometry"]
    infra_records_gdf.append(rec)

infra_gdf = gpd.GeoDataFrame(infra_records_gdf, crs=CRS)

# merge infrastructure + building edges — same pattern as power.py
all_edges_gdf = gpd.GeoDataFrame(
    pd.concat([infra_gdf, building_edges_gdf], ignore_index=True), crs=CRS
)
all_edges_gdf.to_file(f"{OUTPUT_DIR}/water_edges.gpkg", driver="GPKG")
print(f"  Saved: {OUTPUT_DIR}/water_edges.gpkg")

# Step 6 — export JSON
print("\n=== STEP 6: Export JSON ===")
export_water_json(all_nodes, infra_edges, building_edges_gdf, f"{OUTPUT_DIR}/water.json")

print(f"\n  Total nodes : {len(all_nodes)}")
print(f"  Total edges : {len(all_edges_gdf)}")


# ══════════════════════════════════════════════════════════════════════════════
# DISPLAY
# ══════════════════════════════════════════════════════════════════════════════

fig, ax = plt.subplots(figsize=(15, 15))
ax.set_facecolor("#1a1a2e")
fig.patch.set_facecolor("#1a1a2e")

linewidth_map = {
    "main_supply":           2.5,
    "tower_feed":            2.0,
    "junction_link":         0.6,
    "building_service_drop": 0.3,
    "building_road_feeder":  0.4,
}
alpha_map = {
    "main_supply":           0.85,
    "tower_feed":            0.85,
    "junction_link":         0.55,
    "building_service_drop": 0.25,
    "building_road_feeder":  0.30,
}

for edge_type, colour in EDGE_COLOURS.items():
    subset = all_edges_gdf[all_edges_gdf["edge_type"] == edge_type]
    if len(subset) == 0:
        continue
    subset.plot(ax=ax, color=colour,
                linewidth=linewidth_map.get(edge_type, 1.0),
                alpha=alpha_map.get(edge_type, 0.6),
                label=edge_type, zorder=2)

for node_type, colour in NODE_COLOURS.items():
    subset = nodes_gdf[nodes_gdf["node_type"] == node_type]
    if len(subset) == 0:
        continue
    size = {"pump_station": 140, "water_tower": 100, "pipe_junction": 20}.get(node_type, 40)
    subset.plot(ax=ax, color=colour, markersize=size,
                alpha=0.95, label=node_type, zorder=5)

ax.legend(loc="lower left", facecolor="#0f0f23", edgecolor="#444",
          labelcolor="white", fontsize=9)
ax.set_title("Water Distribution Network — Halasuru, Bengaluru",
             color="white", fontsize=16, pad=12)
ax.tick_params(colors="white")
for spine in ax.spines.values():
    spine.set_edgecolor("#444")

plt.tight_layout()
plt.show()


# ══════════════════════════════════════════════════════════════════════════════
# CASCADE LOGIC — WATER NETWORK (agent reference)
# ══════════════════════════════════════════════════════════════════════════════

# ── Equation 1 — Pump pressure decay after power loss ────────────────────────
# P(t) = P0 × exp(−K × t),  K = 0.085 per minute
# Triggered when pump_station.on_grid_power = False AND backup_gen_remaining_h = 0
# if pump.on_grid_power == False and pump.backup_gen_remaining_h <= 0:
#     t_minutes = ticks_since_power_loss * tick_duration_minutes
#     pump.pressure = pump.initial_pressure * exp(-K * t_minutes)
#     if pump.pressure < LOW_PRESSURE_THRESHOLD:
#         emit NODE_DEGRADED to all directly connected junctions

# ── Equation 2 — Hazen-Williams pipe friction ─────────────────────────────────
# loss_fraction = 6.82e-4 × L / (D^1.165) × (140 / C_eff)
# C_eff = C_new − 1.0 × pipe_age_years,  capped at 60
# pressure_at_downstream = pressure_upstream × (1 − loss_fraction)

# ── Equation 3 — Pressure-driven demand (Wagner model) ────────────────────────
# q = q_full × sqrt((P − P_min) / (P_des − P_min)),  P_min=0.05, P_des=1.00

# ── Equation 4 — Hydraulic head pressure for water towers ─────────────────────
# hydraulic_head_pressure = water_level  (reference_head = tower_height_m)
# tower.pressure = tower.water_level  (updated every tick)

# ── Equation 5 — Water tower drain rate ───────────────────────────────────────
# water_level(t+1) = water_level(t) − (base_flow × demand_multiplier) / storage_capacity_m3
# Activated when fill_source_node pump is offline.
# Cascade when water_level < TOWER_DRAIN_THRESHOLD (0.20)

# ── Equation 6 — Pipe burst probability under flood ───────────────────────────
# burst_probability = 1 − exp(−λ × flood_severity × age_factor)
# λ=0.15, age_factor = pipe_age_years / 20.0
# If fires: junction.pressure=0, junction.burst_occurred=True,
#           junction.health -= uniform(0.4,0.6), emit NODE_FAILED downstream

# ── Cross-node cascade ────────────────────────────────────────────────────────
# health drops when pressure < 0.40 for >3 ticks: health -= 0.10/tick
# health < 0.30 → emit NODE_DEGRADED downstream × 1.5 severity
# failed when pressure=0 for 5+ ticks OR burst_occurred=True
# junctions with in-degree >= 2 degrade only (not fail) on single upstream loss

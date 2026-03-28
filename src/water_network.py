"""
Phase 1 - Step 3: Water graph + cross-network dependency edges
Water: synthetic pipe network generated along road buffers + OSM waterways
Dependencies: power->water, telecom->road signals, etc.
"""

import osmnx as ox
import geopandas as gpd
import networkx as nx
import pandas as pd
import numpy as np
from shapely.geometry import Point, LineString
from shapely.ops import unary_union
from scipy.spatial import cKDTree
import os

INDIRANAGAR_CENTER = (12.9784, 77.6408)
RADIUS_M = 1500
CRS = "EPSG:32643"
OUTPUT_DIR = "graphs"
DATA_DIR = "data"


# ══════════════════════════════════════════════════════════════════════════════
# WATER GRAPH
# ══════════════════════════════════════════════════════════════════════════════

def build_water_graph(road_graph=None):
    """
    Water distribution network.

    Strategy:
    1. Pull OSM waterway features (canals, drains) for real geometry reference
    2. Generate synthetic water mains along major road buffers
       (standard practice — BWSSB pipe data is not publicly available)
    3. Place pumping stations at realistic locations
    4. Connect pipes in a tree topology from main reservoir outward
    """
    print("\n=== STEP 3A: Water graph ===")

    from pyproj import Transformer
    transformer = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)

    G_water = nx.Graph()

    # ── pull OSM waterway features for reference ──────────────────────────────
    print("  Pulling OSM waterway features...")
    try:
        waterways = ox.features_from_point(
            INDIRANAGAR_CENTER,
            tags={"waterway": True, "natural": "water"},
            dist=RADIUS_M
        )
        waterways = waterways.to_crs(CRS)
        print(f"  Found {len(waterways)} waterway features")
        waterways.to_file(f"{DATA_DIR}/waterways.gpkg", driver="GPKG")
    except Exception as e:
        print(f"  No waterway OSM data ({e})")
        waterways = gpd.GeoDataFrame()

    # ── define pumping stations (BWSSB) ───────────────────────────────────────
    # Known BWSSB pump station approximate locations in Indiranagar area
    pump_stations = [
        {"id": "WP-MAIN",   "lat": 12.9784, "lon": 77.6408, "name": "Indiranagar main pump"},
        {"id": "WP-NORTH",  "lat": 12.9870, "lon": 77.6390, "name": "North zone pump"},
        {"id": "WP-SOUTH",  "lat": 12.9700, "lon": 77.6420, "name": "South zone pump"},
        {"id": "WP-EAST",   "lat": 12.9790, "lon": 77.6500, "name": "East zone pump"},
        {"id": "WP-ULSOOR", "lat": 12.9760, "lon": 77.6200, "name": "Ulsoor pump station"},
        {"id": "WP-DOMLUR", "lat": 12.9630, "lon": 77.6320, "name": "Domlur pump"},
    ]

    for ps in pump_stations:
        x, y = transformer.transform(ps["lon"], ps["lat"])
        G_water.add_node(
            ps["id"],
            x=x, y=y,
            network="water",
            health=1.0,
            criticality=9.0,        # pumps are extremely critical
            flood_risk=False,
            pop_density=1.0,
            pressure=1.0,           # pressure level (0-1)
            flow_rate=1.0,
            node_type="pump_station",
            name=ps["name"],
            # dependency: pump needs power to run
            power_dependency=None   # will be set in dependency edges step
        )

    # ── generate pipe network nodes along major roads ─────────────────────────
    # Pull road network to use as pipe routing skeleton
    print("  Generating pipe network along road skeleton...")

    if road_graph is not None:
        G_road = road_graph
    else:
        # load from saved file if not passed in
        import osmnx as ox
        G_road = ox.load_graphml(f"{OUTPUT_DIR}/road_graph.graphml")

    # sample pipe junction nodes every ~200m along major roads
    # use road nodes as junction points (realistic — pipes follow roads)
    road_nodes_data = [(n, d) for n, d in G_road.nodes(data=True)]

    # subsample: take every 3rd road node to avoid too many water nodes
    pipe_junctions = road_nodes_data[::3]

    for road_nid, road_data in pipe_junctions:
        junction_id = f"WJ-{road_nid}"
        G_water.add_node(
            junction_id,
            x=road_data["x"],
            y=road_data["y"],
            network="water",
            health=1.0,
            criticality=1.0,
            flood_risk=False,
            pop_density=1.0,
            pressure=1.0,
            flow_rate=0.5,
            node_type="pipe_junction",
            road_node_ref=road_nid
        )

    print(f"  Water nodes: {G_water.number_of_nodes()} (pumps + pipe junctions)")

    # ── connect nodes ─────────────────────────────────────────────────────────
    # 1. Connect each pump to its 3 nearest pipe junctions
    pump_ids = [ps["id"] for ps in pump_stations]
    junction_ids = [n for n in G_water.nodes() if n.startswith("WJ-")]

    all_ids = list(G_water.nodes())
    coords = np.array([[G_water.nodes[n]["x"], G_water.nodes[n]["y"]] for n in all_ids])
    tree = cKDTree(coords)

    for pump_id in pump_ids:
        pump_idx = all_ids.index(pump_id)
        k = min(4, len(all_ids))
        dists, idxs = tree.query(coords[pump_idx], k=k)
        for dist, j in zip(dists[1:], idxs[1:]):
            neighbor_id = all_ids[j]
            if not G_water.has_edge(pump_id, neighbor_id):
                G_water.add_edge(
                    pump_id, neighbor_id,
                    length=float(dist),
                    health=1.0,
                    pressure=1.0,
                    diameter_mm=300,     # main distribution pipe
                    network="water",
                    pipe_type="main"
                )

    # 2. Connect pipe junctions along road edges
    if road_graph is not None:
        for u, v, edge_data in G_road.edges(data=True):
            wj_u = f"WJ-{u}"
            wj_v = f"WJ-{v}"
            if G_water.has_node(wj_u) and G_water.has_node(wj_v):
                if not G_water.has_edge(wj_u, wj_v):
                    length = edge_data.get("length", 100.0)
                    G_water.add_edge(
                        wj_u, wj_v,
                        length=float(length),
                        health=1.0,
                        pressure=0.8,
                        diameter_mm=150,    # secondary distribution pipe
                        network="water",
                        pipe_type="secondary"
                    )

    print(f"  Water graph: {G_water.number_of_nodes()} nodes, {G_water.number_of_edges()} edges")

    # ── save ──────────────────────────────────────────────────────────────────
    node_records = []
    for nid, data in G_water.nodes(data=True):
        d = {k: v for k, v in data.items() if k != "power_dependency"}
        node_records.append({"node_id": nid, "geometry": Point(data["x"], data["y"]), **d})
    nodes_gdf = gpd.GeoDataFrame(node_records, crs=CRS)

    edge_records = []
    for u, v, data in G_water.edges(data=True):
        x1, y1 = G_water.nodes[u]["x"], G_water.nodes[u]["y"]
        x2, y2 = G_water.nodes[v]["x"], G_water.nodes[v]["y"]
        edge_records.append({
            "from": u, "to": v,
            "geometry": LineString([(x1, y1), (x2, y2)]),
            **data
        })
    edges_gdf = gpd.GeoDataFrame(edge_records, crs=CRS)

    nodes_gdf.to_file(f"{OUTPUT_DIR}/water_nodes.gpkg", driver="GPKG")
    edges_gdf.to_file(f"{OUTPUT_DIR}/water_edges.gpkg", driver="GPKG")
    print(f"  Saved: {OUTPUT_DIR}/water_nodes.gpkg + water_edges.gpkg")

    return G_water, nodes_gdf, edges_gdf


# ══════════════════════════════════════════════════════════════════════════════
# CROSS-NETWORK DEPENDENCY EDGES
# ══════════════════════════════════════════════════════════════════════════════

def build_dependency_edges(G_road, G_power, G_water, G_telecom):
    """
    Define cross-network dependency edges.
    These are NOT graph edges within a network — they are a separate
    dependency registry that agents use to trigger cascades.

    Dependency types:
    - power -> water:   water pumps need electricity to run
    - power -> road:    traffic signals need electricity
    - telecom -> road:  smart signals need telecom control
    - telecom -> power: SCADA control of substations needs telecom
    """
    print("\n=== STEP 3B: Cross-network dependency edges ===")

    from scipy.spatial import cKDTree
    import numpy as np

    dependencies = []

    # ── power -> water: each pump depends on nearest substation ──────────────
    power_nodes = [(n, d) for n, d in G_power.nodes(data=True)]
    water_pumps = [(n, d) for n, d in G_water.nodes(data=True)
                   if d.get("node_type") == "pump_station"]

    if power_nodes and water_pumps:
        power_coords = np.array([[d["x"], d["y"]] for _, d in power_nodes])
        power_ids = [n for n, _ in power_nodes]
        tree_power = cKDTree(power_coords)

        for pump_id, pump_data in water_pumps:
            pump_coord = np.array([[pump_data["x"], pump_data["y"]]])
            dist, idx = tree_power.query(pump_coord, k=1)
            nearest_substation = power_ids[idx[0]]
            dependencies.append({
                "dep_id": f"DEP-PW-{pump_id}",
                "from_network": "power",
                "from_node": nearest_substation,
                "to_network": "water",
                "to_node": pump_id,
                "dep_type": "power_supply",
                "failure_probability": 0.95,  # if substation fails, 95% chance pump fails
                "delay_minutes": 2.0,          # pump fails 2 min after power loss
                "description": f"Pump {pump_id} powered by substation {nearest_substation}"
            })

        print(f"  Power->Water dependencies: {len(water_pumps)}")

    # ── power -> road: traffic signals depend on power ────────────────────────
    road_nodes = [(n, d) for n, d in G_road.nodes(data=True)]
    road_coords = np.array([[d["x"], d["y"]] for _, d in road_nodes])
    road_ids = [n for n, _ in road_nodes]

    if power_nodes:
        # signals at major intersections (nodes with degree >= 3 = intersection)
        signal_nodes = [
            (n, d) for n, d in G_road.nodes(data=True)
            if G_road.degree(n) >= 3
        ]
        # subsample — not every intersection has a signal
        signal_nodes = signal_nodes[::5]

        signal_power_count = 0
        if power_nodes:
            tree_power2 = cKDTree(power_coords)
            for sig_id, sig_data in signal_nodes:
                sig_coord = np.array([[sig_data["x"], sig_data["y"]]])
                dist, idx = tree_power2.query(sig_coord, k=1)
                nearest_sub = power_ids[idx[0]]
                dependencies.append({
                    "dep_id": f"DEP-PR-{sig_id}",
                    "from_network": "power",
                    "from_node": nearest_sub,
                    "to_network": "road",
                    "to_node": sig_id,
                    "dep_type": "traffic_signal_power",
                    "failure_probability": 0.90,
                    "delay_minutes": 0.5,
                    "description": f"Traffic signal at {sig_id} powered by {nearest_sub}"
                })
                signal_power_count += 1

        print(f"  Power->Road dependencies: {signal_power_count} traffic signals")

    # ── telecom -> road: smart signal control ─────────────────────────────────
    telecom_nodes = [(n, d) for n, d in G_telecom.nodes(data=True)]
    if telecom_nodes and signal_nodes:
        telecom_coords = np.array([[d["x"], d["y"]] for _, d in telecom_nodes])
        telecom_ids = [n for n, _ in telecom_nodes]
        tree_telecom = cKDTree(telecom_coords)

        telecom_road_count = 0
        for sig_id, sig_data in signal_nodes[:20]:  # top 20 intersections
            sig_coord = np.array([[sig_data["x"], sig_data["y"]]])
            dist, idx = tree_telecom.query(sig_coord, k=1)
            nearest_tower = telecom_ids[idx[0]]
            dependencies.append({
                "dep_id": f"DEP-TR-{sig_id}",
                "from_network": "telecom",
                "from_node": nearest_tower,
                "to_network": "road",
                "to_node": sig_id,
                "dep_type": "signal_control",
                "failure_probability": 0.70,
                "delay_minutes": 5.0,
                "description": f"Smart signal {sig_id} controlled via tower {nearest_tower}"
            })
            telecom_road_count += 1

        print(f"  Telecom->Road dependencies: {telecom_road_count} smart signals")

    # ── telecom -> power: SCADA control ───────────────────────────────────────
    if telecom_nodes and power_nodes:
        tree_telecom2 = cKDTree(telecom_coords)
        scada_count = 0
        for sub_id, sub_data in power_nodes:
            sub_coord = np.array([[sub_data["x"], sub_data["y"]]])
            dist, idx = tree_telecom2.query(sub_coord, k=1)
            nearest_tower = telecom_ids[idx[0]]
            dependencies.append({
                "dep_id": f"DEP-TP-{sub_id}",
                "from_network": "telecom",
                "from_node": nearest_tower,
                "to_network": "power",
                "to_node": sub_id,
                "dep_type": "scada_control",
                "failure_probability": 0.40,  # substations have local backup control
                "delay_minutes": 10.0,
                "description": f"Substation {sub_id} SCADA via tower {nearest_tower}"
            })
            scada_count += 1

        print(f"  Telecom->Power dependencies: {scada_count} SCADA links")

    # ── save dependency registry ───────────────────────────────────────────────
    import json
    with open(f"{DATA_DIR}/dependency_edges.json", "w") as f:
        json.dump(dependencies, f, indent=2)

    dep_df = pd.DataFrame(dependencies)
    dep_df.to_csv(f"{DATA_DIR}/dependency_edges.csv", index=False)

    print(f"\n  Total dependencies: {len(dependencies)}")
    print(f"  Saved: {DATA_DIR}/dependency_edges.json + dependency_edges.csv")

    return dependencies


if __name__ == "__main__":
    import osmnx as ox
    from graph_builder import build_road_graph
    from power_telecom import build_power_graph, build_telecom_graph

    G_road, buildings = build_road_graph()
    G_power, pn, pe = build_power_graph(buildings)
    G_telecom, tn, te = build_telecom_graph()
    G_water, wn, we = build_water_graph(G_road)
    deps = build_dependency_edges(G_road, G_power, G_water, G_telecom)
    print("\nStep 3 complete.")
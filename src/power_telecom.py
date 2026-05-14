"""
Phase 1 - Step 2: Power graph + Telecom graph

Power:
  - Nodes: OSM substations, transformers, generators, plants + known BESCOM substations
  - Edges: real OSM power line/cable geometry; road-network fallback for gaps

Telecom:
  - Nodes: OSM towers, exchanges, data centres, cabinets
  - Edges: real OSM telecom line geometry; road-network fallback for gaps
    (cables run along road corridors — road graph is the physical routing backbone)
"""

import osmnx as ox
import geopandas as gpd
import networkx as nx
import numpy as np
from shapely.geometry import Point, LineString
from scipy.spatial import cKDTree
from pyproj import Transformer
import os

HALASURU_CENTER = (12.9762, 77.6265)
RADIUS_M        = 2000
CRS             = "EPSG:32643"
OUTPUT_DIR      = "graphs"
DATA_DIR        = "data"

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(DATA_DIR,   exist_ok=True)

WGS84_TO_UTM = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)


# ══════════════════════════════════════════════════════════════════════════════
# SHARED UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def snap_to_road(G_road, x, y):
    """Return nearest road node id to a given projected coordinate."""
    return ox.nearest_nodes(G_road, x, y)


def road_path_geometry(G_road, road_node_u, road_node_v):
    """
    Shortest road path between two road nodes returned as (LineString, length).
    This is the physical cable/line route — cables follow road corridors.
    Returns (None, None) if no path exists.
    """
    try:
        path   = nx.shortest_path(G_road, road_node_u, road_node_v, weight="length")
        coords = [(G_road.nodes[n]["x"], G_road.nodes[n]["y"]) for n in path]
        length = sum(
            G_road.edges[path[i], path[i+1], 0].get("length", 0)
            for i in range(len(path) - 1)
        )
        return LineString(coords), length
    except nx.NetworkXNoPath:
        return None, None


def save_graph_to_gpkg(G, node_path, edge_path, crs):
    """Serialise a networkx Graph to two GeoPackage files."""
    node_records = [
        {"node_id": nid, "geometry": Point(d["x"], d["y"]), **d}
        for nid, d in G.nodes(data=True)
    ]
    nodes_gdf = gpd.GeoDataFrame(node_records, crs=crs)

    edge_records = []
    for u, v, d in G.edges(data=True):
        geom = d.get("geometry", LineString([
            (G.nodes[u]["x"], G.nodes[u]["y"]),
            (G.nodes[v]["x"], G.nodes[v]["y"]),
        ]))
        edge_records.append({
            "from": u, "to": v, "geometry": geom,
            **{k: val for k, val in d.items() if k != "geometry"},
        })

    if edge_records:
        edges_gdf = gpd.GeoDataFrame(edge_records, crs=crs)
    else:
        edges_gdf = gpd.GeoDataFrame({"from": [], "to": [], "geometry": []})
        edges_gdf = edges_gdf.set_geometry("geometry")
        edges_gdf.crs = crs

    nodes_gdf.to_file(node_path, driver="GPKG")
    edges_gdf.to_file(edge_path, driver="GPKG")
    return nodes_gdf, edges_gdf


def connect_components_via_road(G, G_road):
    """
    Bridge isolated components by routing along the road network.
    Modifies G in place. Returns count of fallback edges added.
    """
    components = list(nx.connected_components(G))
    if len(components) == 1:
        return 0

    components.sort(key=len, reverse=True)
    main_ids    = list(components[0])
    main_coords = np.array([[G.nodes[n]["x"], G.nodes[n]["y"]] for n in main_ids])
    tree_main   = cKDTree(main_coords)
    added       = 0

    for comp in components[1:]:
        for iso_id in comp:
            _, idx       = tree_main.query([G.nodes[iso_id]["x"], G.nodes[iso_id]["y"]], k=1)
            target_id    = main_ids[idx]
            snap_u       = G.nodes[iso_id]["road_snap_node"]
            snap_v       = G.nodes[target_id]["road_snap_node"]
            geom, length = road_path_geometry(G_road, snap_u, snap_v)

            if geom is None:        # last resort: straight line
                geom   = LineString([
                    (G.nodes[iso_id]["x"],   G.nodes[iso_id]["y"]),
                    (G.nodes[target_id]["x"], G.nodes[target_id]["y"]),
                ])
                length = geom.length

            network = G.nodes[iso_id].get("network", "unknown")
            G.add_edge(
                iso_id, target_id,
                geometry=geom,
                length=float(length),
                health=1.0,
                capacity=1.0,
                network=network,
                edge_type="road_routed",
            )
            added += 1

    return added


# ══════════════════════════════════════════════════════════════════════════════
# POWER GRAPH
# ══════════════════════════════════════════════════════════════════════════════

POWER_NODE_TAGS = {
    "power":    ["substation", "transformer", "generator", "plant"],
    "building": ["data_center"],
    "amenity":  ["fuel"],
}

POWER_LINE_TAGS = {
    "power": ["line", "minor_line", "cable"],
}

POWER_CRITICALITY = {
    "plant":       10.0,   # generation source — tier-1 cascade trigger
    "substation":   8.0,
    "transformer":  7.0,
    "generator":    7.0,   # backup power — finite runtime
    "data_center":  6.0,   # large load; hosts emergency coordination systems
    "fuel":         5.0,   # loses function without power
    "default":      5.0,
}

# real approximate BESCOM substation locations (Halasuru / Indiranagar)
KNOWN_SUBSTATIONS = [
    {"node_id": "PS-CMH",    "lon": 77.6489, "lat": 12.9716, "name": "CMH Road substation"},
    {"node_id": "PS-100FT",  "lon": 77.6408, "lat": 12.9784, "name": "100 Feet Road substation"},
    {"node_id": "PS-INDNR",  "lon": 77.6380, "lat": 12.9850, "name": "Indiranagar 1st stage"},
    {"node_id": "PS-DOMLUR", "lon": 77.6320, "lat": 12.9630, "name": "Domlur substation"},
    {"node_id": "PS-HAL",    "lon": 77.6560, "lat": 12.9900, "name": "HAL 2nd stage"},
    {"node_id": "PS-ULSOOR", "lon": 77.6200, "lat": 12.9780, "name": "Ulsoor substation"},
]


def classify_power_node(row):
    power    = str(row.get("power",    "")).lower().strip()
    building = str(row.get("building", "")).lower().strip()
    amenity  = str(row.get("amenity",  "")).lower().strip()
    if power in POWER_CRITICALITY:
        return power
    if building == "data_center":
        return "data_center"
    if amenity == "fuel":
        return "fuel"
    return "default"


def build_power_graph(G_road):
    print("\n=== STEP 2A: Power graph ===")
    G_power = nx.Graph()

    # ── 1. OSM power node features ────────────────────────────────────────────
    print("Pulling power node features from OSM...")
    try:
        pf = ox.features_from_point(HALASURU_CENTER, tags=POWER_NODE_TAGS, dist=RADIUS_M)
        pf = pf.to_crs(CRS)
        print(f"  OSM power features (raw): {len(pf)}")
    except Exception as e:
        print(f"  OSM power node query failed ({e})")
        pf = gpd.GeoDataFrame()

    osm_nodes = []
    for _, row in (pf.iterrows() if len(pf) > 0 else []):
        node_type = classify_power_node(row)
        centroid  = row.geometry.centroid
        osm_nodes.append({
            "node_id":   f"PW-OSM-{len(osm_nodes)+1}",
            "x":         centroid.x,
            "y":         centroid.y,
            "source":    "osm",
            "node_type": node_type,
            "voltage":   row.get("voltage",  "11kV"),
            "operator":  row.get("operator", "BESCOM"),
            "name":      row.get("name",     ""),
        })

    # ── 2. merge known BESCOM substations, deduplicate at 100 m ──────────────
    all_nodes = []
    for sub in KNOWN_SUBSTATIONS:
        x_proj, y_proj = WGS84_TO_UTM.transform(sub["lon"], sub["lat"])
        all_nodes.append({
            "node_id": sub["node_id"], "x": x_proj, "y": y_proj,
            "source": "manual", "node_type": "substation",
            "voltage": "11kV", "operator": "BESCOM", "name": sub["name"],
        })
    for n in osm_nodes:
        if not any(((n["x"]-e["x"])**2 + (n["y"]-e["y"])**2)**0.5 < 100 for e in all_nodes):
            all_nodes.append(n)

    print(f"  Power nodes total: {len(all_nodes)}")

    # ── 3. add nodes ──────────────────────────────────────────────────────────
    for node in all_nodes:
        crit      = POWER_CRITICALITY.get(node["node_type"], POWER_CRITICALITY["default"])
        road_snap = snap_to_road(G_road, node["x"], node["y"])
        G_power.add_node(
            node["node_id"],
            x=node["x"], y=node["y"],
            network="power",
            node_type=node["node_type"],
            health=1.0,
            criticality=crit,
            load=float(np.random.uniform(0.55, 0.85)),   # ADD - was 0.4-0.9, change range
            base_load=float(np.random.uniform(0.55, 0.85)),  # ADD
            max_load=1.0,
            voltage_kv=11.0,
            voltage_deviation=0.0,
            overload_count=0,
            vulnerability=0.0,
            is_island=False,
            has_backup_gen=node["source"] == "manual",   # ADD - manual = BESCOM known substations
            backup_gen_runtime=float(np.random.uniform(3.0, 6.0)) if node["source"] == "manual" else 0.0,  # ADD
            scada_linked=True,
            scada_coverage_ok=True,
            pending_cascades="[]",       # ADD - stored as string for graphml
            cascade_description="",      # ADD
            health_history="[1.0,1.0,1.0,1.0,1.0]",  # ADD - stored as string
            flood_risk=False,
            pop_density=1.0,
            frequency_hz=50.0,           # ADD
            power_factor=0.9,            # ADD
            # keep these from Phase 1:
            operator=node.get("operator", "BESCOM"),
            name=node.get("name", ""),
            road_snap_node=road_snap,
        )

    # ── 4. real OSM power line geometries ─────────────────────────────────────
    print("Pulling power line geometries from OSM...")
    real_edges = 0
    try:
        lf = ox.features_from_point(HALASURU_CENTER, tags=POWER_LINE_TAGS, dist=RADIUS_M)
        lf = lf.to_crs(CRS)
        lf = lf[lf.geometry.geom_type.isin(["LineString", "MultiLineString"])].copy()
        print(f"  OSM power lines (raw): {len(lf)}")

        node_ids = list(G_power.nodes())
        coords   = np.array([[G_power.nodes[n]["x"], G_power.nodes[n]["y"]] for n in node_ids])
        tree     = cKDTree(coords)

        for _, lr in lf.iterrows():
            geom = lr.geometry
            if geom.geom_type == "MultiLineString":
                geom = geom.geoms[0]
            d_s, i_s = tree.query([geom.coords[0][0],  geom.coords[0][1]],  k=1)
            d_e, i_e = tree.query([geom.coords[-1][0], geom.coords[-1][1]], k=1)
            if d_s < 150 and d_e < 150 and i_s != i_e:
                u, v = node_ids[i_s], node_ids[i_e]
                if not G_power.has_edge(u, v):
                    G_power.add_edge(u, v,
                        geometry=geom, length=float(geom.length),
                        health=1.0, capacity=1.0,
                        voltage=lr.get("voltage", "11kV"),
                        network="power", edge_type="osm_line",
                    )
                    real_edges += 1
    except Exception as e:
        print(f"  OSM power line query failed ({e})")

    print(f"  Real OSM power edges: {real_edges}")

    # ── 5. road-network fallback for remaining gaps ───────────────────────────
    fallback = connect_components_via_road(G_power, G_road)
    print(f"  Road-routed fallback edges: {fallback}")
    print(f"  Power graph: {G_power.number_of_nodes()} nodes, {G_power.number_of_edges()} edges | connected: {nx.is_connected(G_power)}")

    # ── 6. save ───────────────────────────────────────────────────────────────
    nodes_gdf, edges_gdf = save_graph_to_gpkg(
        G_power,
        f"{OUTPUT_DIR}/power_nodes.gpkg",
        f"{OUTPUT_DIR}/power_edges.gpkg",
        CRS,
    )
    print(f"  Saved: {OUTPUT_DIR}/power_nodes.gpkg + power_edges.gpkg")
    return G_power, nodes_gdf, edges_gdf


# ══════════════════════════════════════════════════════════════════════════════
# TELECOM GRAPH
# ══════════════════════════════════════════════════════════════════════════════

TELECOM_NODE_TAGS = {
    "man_made": ["mast", "tower"],
    "telecom":  ["data_center", "exchange", "cabinet", "terminal"],
    "building": ["data_center"],
}

TELECOM_LINE_TAGS = {
    "telecom": ["line"],
}

TELECOM_CRITICALITY = {
    "exchange":    9.0,   # single point of failure — cuts entire zone if down
    "data_center": 8.0,   # hosts emergency coordination + cloud systems
    "mast":        5.0,
    "tower":       5.0,
    "cabinet":     3.0,   # street-level distribution — numerous but low individual impact
    "terminal":    3.0,
    "default":     4.0,
}

# synthetic fallback towers (realistic Halasuru / Indiranagar positions)
SYNTHETIC_TOWERS = [
    {"lat": 12.9784, "lon": 77.6408, "operator": "Jio",    "node_type": "mast"},
    {"lat": 12.9830, "lon": 77.6450, "operator": "Airtel", "node_type": "mast"},
    {"lat": 12.9720, "lon": 77.6370, "operator": "Vi",     "node_type": "mast"},
    {"lat": 12.9860, "lon": 77.6380, "operator": "BSNL",   "node_type": "mast"},
    {"lat": 12.9750, "lon": 77.6500, "operator": "Jio",    "node_type": "mast"},
    {"lat": 12.9900, "lon": 77.6430, "operator": "Airtel", "node_type": "mast"},
    {"lat": 12.9680, "lon": 77.6440, "operator": "Vi",     "node_type": "mast"},
    {"lat": 12.9810, "lon": 77.6340, "operator": "Jio",    "node_type": "mast"},
]


def classify_telecom_node(row):
    telecom    = str(row.get("telecom",    "")).lower().strip()
    building   = str(row.get("building",  "")).lower().strip()
    man_made   = str(row.get("man_made",  "")).lower().strip()
    tower_type = str(row.get("tower:type","")).lower().strip()

    if telecom == "exchange":
        return "exchange"
    if telecom == "data_center" or building == "data_center":
        return "data_center"
    if telecom == "cabinet":
        return "cabinet"
    if telecom == "terminal":
        return "terminal"
    if man_made in ("mast", "tower") and tower_type == "communication":
        return man_made
    return None     # non-communication mast/tower — discard


def build_telecom_graph(G_road):
    print("\n=== STEP 2B: Telecom graph ===")
    G_telecom = nx.Graph()

    # ── 1. OSM telecom node features ─────────────────────────────────────────
    print("Pulling telecom node features from OSM...")
    try:
        tf = ox.features_from_point(HALASURU_CENTER, tags=TELECOM_NODE_TAGS, dist=RADIUS_M)
        tf = tf.to_crs(CRS)
        print(f"  OSM telecom features (raw): {len(tf)}")
    except Exception as e:
        print(f"  OSM telecom node query failed ({e})")
        tf = gpd.GeoDataFrame()

    osm_nodes = []
    for _, row in (tf.iterrows() if len(tf) > 0 else []):
        node_type = classify_telecom_node(row)
        if node_type is None:
            continue
        centroid = row.geometry.centroid
        osm_nodes.append({
            "node_id":   f"TC-OSM-{len(osm_nodes)+1}",
            "x":         centroid.x,
            "y":         centroid.y,
            "source":    "osm",
            "node_type": node_type,
            "operator":  row.get("operator", "unknown"),
            "name":      row.get("name",     ""),
        })
    print(f"  OSM telecom nodes (filtered): {len(osm_nodes)}")

    # ── 2. synthetic fallback if OSM is sparse ────────────────────────────────
    if len(osm_nodes) < 3:
        print("  Fewer than 3 OSM nodes — supplementing with synthetic towers")
        for i, t in enumerate(SYNTHETIC_TOWERS):
            x_proj, y_proj = WGS84_TO_UTM.transform(t["lon"], t["lat"])
            osm_nodes.append({
                "node_id":   f"TC-SYN-{i+1}",
                "x":         x_proj,
                "y":         y_proj,
                "source":    "synthetic",
                "node_type": t["node_type"],
                "operator":  t["operator"],
                "name":      "",
            })

    # ── 3. add nodes ──────────────────────────────────────────────────────────
    for node in osm_nodes:
        crit      = TELECOM_CRITICALITY.get(node["node_type"], TELECOM_CRITICALITY["default"])
        road_snap = snap_to_road(G_road, node["x"], node["y"])
        G_telecom.add_node(
            node["node_id"],
            x=node["x"], y=node["y"],
            network="telecom",
            node_type=node["node_type"],
            health=1.0,
            criticality=crit,
            # Phase 3 additions:
            signal_strength=1.0,         # ADD
            in_coverage_gap=False,       # ADD
            packet_loss_fraction=0.0,    # ADD
            latency_ms=5.0,              # ADD
            backhaul_ok=True,            # ADD
            connected_towers="[]",       # ADD - as string for graphml
            power_source="grid",         # ADD
            has_backup_gen=False,        # ADD
            backup_gen_runtime_h=0.0,    # ADD
            is_scada_node=False,         # ADD - set later by interdependency.py
            scada_substations="[]",      # ADD
            pending_cascades="[]",       # ADD
            cascade_description="",      # ADD
            health_history="[1.0,1.0,1.0,1.0,1.0]",  # ADD
            flood_risk=False,
            pop_density=1.0,
            coverage_radius_m=500,
            operator=node.get("operator", "unknown"),
            source=node.get("source", "osm"),
            name=node.get("name", ""),
            road_snap_node=road_snap,
        )

    # ── 4. real OSM telecom line geometries ───────────────────────────────────
    print("Pulling telecom line geometries from OSM...")
    real_edges = 0
    try:
        lf = ox.features_from_point(HALASURU_CENTER, tags=TELECOM_LINE_TAGS, dist=RADIUS_M)
        lf = lf.to_crs(CRS)
        lf = lf[lf.geometry.geom_type.isin(["LineString", "MultiLineString"])].copy()
        print(f"  OSM telecom lines (raw): {len(lf)}")

        node_ids = list(G_telecom.nodes())
        coords   = np.array([[G_telecom.nodes[n]["x"], G_telecom.nodes[n]["y"]] for n in node_ids])
        tree     = cKDTree(coords)

        for _, lr in lf.iterrows():
            geom = lr.geometry
            if geom.geom_type == "MultiLineString":
                geom = geom.geoms[0]
            d_s, i_s = tree.query([geom.coords[0][0],  geom.coords[0][1]],  k=1)
            d_e, i_e = tree.query([geom.coords[-1][0], geom.coords[-1][1]], k=1)
            if d_s < 150 and d_e < 150 and i_s != i_e:
                u, v = node_ids[i_s], node_ids[i_e]
                if not G_telecom.has_edge(u, v):
                    G_telecom.add_edge(u, v,
                        geometry=geom, length=float(geom.length),
                        health=1.0,
                        latency_ms=float(geom.length / 200_000 * 1000),
                        capacity=1.0,
                        network="telecom",
                        edge_type="osm_line",
                        cable_type="fiber",
                    )
                    real_edges += 1
    except Exception as e:
        print(f"  OSM telecom line query failed ({e})")

    print(f"  Real OSM telecom edges: {real_edges}")

    # ── 5. road-network routing for remaining gaps ────────────────────────────
    fallback = connect_components_via_road(G_telecom, G_road)
    print(f"  Road-routed fallback edges: {fallback}")
    print(f"  Telecom graph: {G_telecom.number_of_nodes()} nodes, {G_telecom.number_of_edges()} edges | connected: {nx.is_connected(G_telecom)}")

    # ── 6. save ───────────────────────────────────────────────────────────────
    nodes_gdf, edges_gdf = save_graph_to_gpkg(
        G_telecom,
        f"{OUTPUT_DIR}/telecom_nodes.gpkg",
        f"{OUTPUT_DIR}/telecom_edges.gpkg",
        CRS,
    )
    print(f"  Saved: {OUTPUT_DIR}/telecom_nodes.gpkg + telecom_edges.gpkg")
    return G_telecom, nodes_gdf, edges_gdf


# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Loading road graph from disk...")
    G_road = ox.load_graphml(f"{OUTPUT_DIR}/road_graph.graphml")
    print(f"  Road graph: {G_road.number_of_nodes()} nodes, {G_road.number_of_edges()} edges")

    G_power,   pn, pe = build_power_graph(G_road)
    G_telecom, tn, te = build_telecom_graph(G_road)

    print("\nStep 2 complete.")
    print(f"  Power:   {G_power.number_of_nodes()} nodes, {G_power.number_of_edges()} edges")
    print(f"  Telecom: {G_telecom.number_of_nodes()} nodes, {G_telecom.number_of_edges()} edges")

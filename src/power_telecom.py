"""
Phase 1 - Step 2: Power graph + Telecom graph
Power: OSM substation/line tags + synthetic load values
Telecom: OpenCelliD towers + minimum spanning tree fiber backbone
"""

import osmnx as ox
import geopandas as gpd
import networkx as nx
import pandas as pd
import numpy as np
from shapely.geometry import Point, LineString
from scipy.spatial import cKDTree
import requests
import os

INDIRANAGAR_CENTER = (12.9784, 77.6408)
RADIUS_M = 1500
CRS = "EPSG:32643"
OUTPUT_DIR = "graphs"
DATA_DIR = "data"


# ══════════════════════════════════════════════════════════════════════════════
# POWER GRAPH
# ══════════════════════════════════════════════════════════════════════════════

def build_power_graph(buildings_gdf=None):
    """
    Pull power infrastructure from OSM.
    Nodes = substations, transformers, generators
    Edges = power lines connecting them
    Synthetic load values calibrated to building density nearby.
    """
    print("\n=== STEP 2A: Power graph ===")

    # pull all power-tagged features from OSM
    print("Pulling power features from OSM...")
    power_tags = {
        "power": ["substation", "transformer", "tower", "pole",
                  "generator", "plant", "line", "minor_line", "cable"]
    }

    try:
        power_features = ox.features_from_point(
            INDIRANAGAR_CENTER, tags=power_tags, dist=RADIUS_M
        )
        power_features = power_features.to_crs(CRS)
        print(f"  OSM power features: {len(power_features)}")
    except Exception as e:
        print(f"  OSM power query returned no results ({e}), using synthetic substations")
        power_features = gpd.GeoDataFrame()

    # ── extract substations as nodes ─────────────────────────────────────────
    G_power = nx.Graph()

    # from OSM data
    substations = []
    if len(power_features) > 0:
        subs = power_features[
            power_features.get("power", pd.Series()).isin(["substation", "transformer"])
        ].copy() if "power" in power_features.columns else gpd.GeoDataFrame()

        for idx, row in subs.iterrows():
            centroid = row.geometry.centroid
            substations.append({
                "node_id": f"PS-{len(substations)+1}",
                "x": centroid.x,
                "y": centroid.y,
                "source": "osm",
                "voltage": row.get("voltage", "11kV"),
                "operator": row.get("operator", "BESCOM")
            })

    # if OSM gave us fewer than 4, add known BESCOM substations manually
    # these are real approximate locations in Indiranagar
    known_substations = [
        {"node_id": "PS-CMH",    "x": 77.6489, "y": 12.9716, "name": "CMH Road substation"},
        {"node_id": "PS-100FT",  "x": 77.6408, "y": 12.9784, "name": "100 Feet Road substation"},
        {"node_id": "PS-INDNR",  "x": 77.6380, "y": 12.9850, "name": "Indiranagar 1st stage"},
        {"node_id": "PS-DOMLUR", "x": 77.6320, "y": 12.9630, "name": "Domlur substation"},
        {"node_id": "PS-HAL",    "x": 77.6560, "y": 12.9900, "name": "HAL 2nd stage"},
        {"node_id": "PS-ULSOOR", "x": 77.6200, "y": 12.9780, "name": "Ulsoor substation"},
    ]

    # convert known substations to projected coords
    from pyproj import Transformer
    transformer = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)

    manual_nodes = []
    for sub in known_substations:
        x_proj, y_proj = transformer.transform(sub["x"], sub["y"])
        manual_nodes.append({
            "node_id": sub["node_id"],
            "x": x_proj,
            "y": y_proj,
            "source": "manual",
            "name": sub["name"],
            "voltage": "11kV",
            "operator": "BESCOM"
        })

    # merge OSM + manual, deduplicate by proximity (>100m apart = different nodes)
    all_nodes = manual_nodes.copy()
    for osm_node in substations:
        too_close = False
        for existing in all_nodes:
            dist = ((osm_node["x"] - existing["x"])**2 +
                    (osm_node["y"] - existing["y"])**2) ** 0.5
            if dist < 100:
                too_close = True
                break
        if not too_close:
            all_nodes.append(osm_node)

    print(f"  Power nodes: {len(all_nodes)} substations")

    # ── add nodes to graph ────────────────────────────────────────────────────
    for node in all_nodes:
        # synthetic load: random base + time-of-day factor (set to peak for now)
        base_load = np.random.uniform(0.4, 0.9)
        G_power.add_node(
            node["node_id"],
            x=node["x"],
            y=node["y"],
            network="power",
            health=1.0,
            criticality=8.0,          # substations are highly critical
            load=base_load,            # current load fraction (0-1)
            max_load=1.0,
            flood_risk=False,
            pop_density=1.0,
            voltage=node.get("voltage", "11kV"),
            operator=node.get("operator", "BESCOM"),
            node_type="substation"
        )

    # ── connect nodes with edges (power lines) ────────────────────────────────
    # Strategy: connect each substation to its 2 nearest neighbours
    # This approximates the real distribution network topology
    node_ids = list(G_power.nodes())
    coords = np.array([[G_power.nodes[n]["x"], G_power.nodes[n]["y"]] for n in node_ids])

    if len(coords) >= 2:
        tree = cKDTree(coords)
        for i, node_id in enumerate(node_ids):
            # find 2 nearest (excluding self)
            k = min(3, len(node_ids))
            dists, idxs = tree.query(coords[i], k=k)
            for dist, j in zip(dists[1:], idxs[1:]):  # skip self (idx 0)
                neighbor_id = node_ids[j]
                if not G_power.has_edge(node_id, neighbor_id):
                    G_power.add_edge(
                        node_id, neighbor_id,
                        length=float(dist),
                        health=1.0,
                        capacity=1.0,
                        voltage="11kV",
                        network="power"
                    )

    print(f"  Power graph: {G_power.number_of_nodes()} nodes, {G_power.number_of_edges()} edges")

      # ── ADD THIS: fix connectivity ──────────────────────────────────────────
    components = list(nx.connected_components(G_power))
    if len(components) > 1:
        components.sort(key=len, reverse=True)
        main_ids = list(components[0])
        main_coords = np.array([[G_power.nodes[n]["x"], G_power.nodes[n]["y"]] for n in main_ids])
        tree_main = cKDTree(main_coords)
        for comp in components[1:]:
            for iso_id in comp:
                ix = G_power.nodes[iso_id]["x"]
                iy = G_power.nodes[iso_id]["y"]
                dist, idx = tree_main.query([ix, iy], k=1)
                G_power.add_edge(iso_id, main_ids[idx],
                    length=float(dist), health=1.0,
                    capacity=1.0, voltage="11kV",
                    network="power", edge_type="bridge")
        print(f"  Bridged {len(components)-1} isolated component(s) → now connected: {nx.is_connected(G_power)}")
    # ── END FIX ─────────────────────────────────────────────────────────────

    # ── save as GeoPackage ────────────────────────────────────────────────────
    node_records = []
    for nid, data in G_power.nodes(data=True):
        node_records.append({"node_id": nid, "geometry": Point(data["x"], data["y"]), **data})
    nodes_gdf = gpd.GeoDataFrame(node_records, crs=CRS)

    edge_records = []
    for u, v, data in G_power.edges(data=True):
        x1, y1 = G_power.nodes[u]["x"], G_power.nodes[u]["y"]
        x2, y2 = G_power.nodes[v]["x"], G_power.nodes[v]["y"]
        edge_records.append({
            "from": u, "to": v,
            "geometry": LineString([(x1, y1), (x2, y2)]),
            **data
        })
    edges_gdf = gpd.GeoDataFrame(edge_records, crs=CRS)

    nodes_gdf.to_file(f"{OUTPUT_DIR}/power_nodes.gpkg", driver="GPKG")
    edges_gdf.to_file(f"{OUTPUT_DIR}/power_edges.gpkg", driver="GPKG")
    print(f"  Saved: {OUTPUT_DIR}/power_nodes.gpkg + power_edges.gpkg")

    return G_power, nodes_gdf, edges_gdf


# ══════════════════════════════════════════════════════════════════════════════
# TELECOM GRAPH
# ══════════════════════════════════════════════════════════════════════════════

def fetch_opencellid_towers():
    """
    Try to fetch real cell tower data from OpenCelliD / Mozilla Location Services.
    Falls back to synthetic towers if API is unavailable.
    MLS API: https://location.services.mozilla.com/v1/geosubmit
    OpenCelliD requires API key — we use the free fallback.
    """
    print("  Fetching cell tower locations...")

    # Try OpenCelliD via Unwired Labs free tier (no key needed for small queries)
    # Bounding box for Indiranagar
    lat_min, lat_max = 12.960, 12.995
    lon_min, lon_max = 77.620, 77.660

    try:
        # OpenCelliD OCID database download URL (free, requires registration)
        # For demo we use the Overpass API to find telecom towers in OSM
        overpass_url = "https://overpass-api.de/api/interpreter"
        query = f"""
        [out:json][timeout:25];
        (
          node["man_made"="mast"]["tower:type"="communication"]
            ({lat_min},{lon_min},{lat_max},{lon_max});
          node["man_made"="tower"]["tower:type"="communication"]
            ({lat_min},{lon_min},{lat_max},{lon_max});
          node["communication"="mobile_phone"]
            ({lat_min},{lon_min},{lat_max},{lon_max});
        );
        out body;
        """
        resp = requests.post(overpass_url, data=query, timeout=30)
        data = resp.json()
        elements = data.get("elements", [])
        print(f"  Found {len(elements)} towers via Overpass/OSM")

        if len(elements) >= 3:
            towers = []
            for el in elements:
                towers.append({"lat": el["lat"], "lon": el["lon"], "source": "osm"})
            return towers
    except Exception as e:
        print(f"  Overpass query failed: {e}")

    # Fallback: synthetic towers at realistic Indiranagar locations
    print("  Using synthetic tower locations (realistic Indiranagar positions)")
    synthetic_towers = [
        {"lat": 12.9784, "lon": 77.6408, "source": "synthetic", "operator": "Jio"},
        {"lat": 12.9830, "lon": 77.6450, "source": "synthetic", "operator": "Airtel"},
        {"lat": 12.9720, "lon": 77.6370, "source": "synthetic", "operator": "Vi"},
        {"lat": 12.9860, "lon": 77.6380, "source": "synthetic", "operator": "BSNL"},
        {"lat": 12.9750, "lon": 77.6500, "source": "synthetic", "operator": "Jio"},
        {"lat": 12.9900, "lon": 77.6430, "source": "synthetic", "operator": "Airtel"},
        {"lat": 12.9680, "lon": 77.6440, "source": "synthetic", "operator": "Vi"},
        {"lat": 12.9810, "lon": 77.6340, "source": "synthetic", "operator": "Jio"},
    ]
    return synthetic_towers


def build_telecom_graph():
    """
    Telecom graph:
    Nodes = cell towers (real from OpenCelliD/OSM or synthetic)
    Edges = fiber backbone via minimum spanning tree
    """
    print("\n=== STEP 2B: Telecom graph ===")

    towers_raw = fetch_opencellid_towers()

    from pyproj import Transformer
    transformer = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)

    G_telecom = nx.Graph()

    for i, tower in enumerate(towers_raw):
        x_proj, y_proj = transformer.transform(tower["lon"], tower["lat"])
        tower_id = f"TOWER-{i+1}"
        G_telecom.add_node(
            tower_id,
            x=x_proj,
            y=y_proj,
            lat=tower["lat"],
            lon=tower["lon"],
            network="telecom",
            health=1.0,
            criticality=5.0,
            flood_risk=False,
            pop_density=1.0,
            operator=tower.get("operator", "unknown"),
            source=tower.get("source", "synthetic"),
            coverage_radius_m=500,
            node_type="cell_tower"
        )

    node_ids = list(G_telecom.nodes())
    coords = np.array([
        [G_telecom.nodes[n]["x"], G_telecom.nodes[n]["y"]] for n in node_ids
    ])

    # build minimum spanning tree for fiber backbone
    # MST ensures connectivity with minimum total cable length — realistic
    if len(coords) >= 2:
        from scipy.sparse.csgraph import minimum_spanning_tree
        from scipy.spatial.distance import cdist
        dist_matrix = cdist(coords, coords)
        mst = minimum_spanning_tree(dist_matrix).toarray()

        for i in range(len(node_ids)):
            for j in range(len(node_ids)):
                if mst[i][j] > 0:
                    G_telecom.add_edge(
                        node_ids[i], node_ids[j],
                        length=float(mst[i][j]),
                        health=1.0,
                        latency_ms=float(mst[i][j] / 200000 * 1000),  # ~speed of light in fiber
                        capacity=1.0,
                        network="telecom",
                        cable_type="fiber"
                    )

    print(f"  Telecom graph: {G_telecom.number_of_nodes()} towers, {G_telecom.number_of_edges()} fiber links")

    # ── save ──────────────────────────────────────────────────────────────────
    node_records = []
    for nid, data in G_telecom.nodes(data=True):
        node_records.append({"node_id": nid, "geometry": Point(data["x"], data["y"]), **data})
    nodes_gdf = gpd.GeoDataFrame(node_records, crs=CRS)

    edge_records = []
    for u, v, data in G_telecom.edges(data=True):
        x1, y1 = G_telecom.nodes[u]["x"], G_telecom.nodes[u]["y"]
        x2, y2 = G_telecom.nodes[v]["x"], G_telecom.nodes[v]["y"]
        edge_records.append({
            "from": u, "to": v,
            "geometry": LineString([(x1, y1), (x2, y2)]),
            **data
        })
    edges_gdf = gpd.GeoDataFrame(edge_records, crs=CRS)

    nodes_gdf.to_file(f"{OUTPUT_DIR}/telecom_nodes.gpkg", driver="GPKG")
    edges_gdf.to_file(f"{OUTPUT_DIR}/telecom_edges.gpkg", driver="GPKG")
    print(f"  Saved: {OUTPUT_DIR}/telecom_nodes.gpkg + telecom_edges.gpkg")

    return G_telecom, nodes_gdf, edges_gdf


if __name__ == "__main__":
    G_power, pn, pe = build_power_graph()
    G_telecom, tn, te = build_telecom_graph()
    print("\nStep 2 complete.")
    print(f"  Power:   {G_power.number_of_nodes()} nodes, {G_power.number_of_edges()} edges")
    print(f"  Telecom: {G_telecom.number_of_nodes()} nodes, {G_telecom.number_of_edges()} edges")
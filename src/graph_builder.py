"""
Phase 1 - Step 1: Road graph + building footprints
Pulls real Indiranagar, Bengaluru data from OpenStreetMap via OSMnx
"""

import osmnx as ox
import geopandas as gpd
import networkx as nx
import os

# ── config ────────────────────────────────────────────────────────────────────
INDIRANAGAR_CENTER = (12.9784, 77.6408)  # 100 Feet Road junction
RADIUS_M = 1500                           # 1.5km radius
CRS = "EPSG:32643"                        # UTM zone 43N for Bengaluru
OUTPUT_DIR = "graphs"
DATA_DIR = "data"

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


def build_road_graph():
    """Pull Indiranagar road network from OSM and save to disk."""
    print("\n=== STEP 1: Road graph + buildings ===")
    print("Pulling road graph from OpenStreetMap...")

    G = ox.graph_from_point(
        INDIRANAGAR_CENTER,
        dist=RADIUS_M,
        network_type="drive",
        simplify=True,
        retain_all=False
    )
    print(f"  Raw graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # project to UTM (metres, not degrees)
    G_proj = ox.project_graph(G, to_crs=CRS)

    # add speed + travel time attributes
    G_proj = ox.add_edge_speeds(G_proj)
    G_proj = ox.add_edge_travel_times(G_proj)

    # initialise simulation health attributes on every node
    for node_id, data in G_proj.nodes(data=True):
        data["health"] = 1.0
        data["network"] = "road"
        data["criticality"] = 1.0
        data["flood_risk"] = False
        data["pop_density"] = 1.0
        data["elevation"] = data.get("elevation", 0.0)

    # initialise simulation health attributes on every edge
    for u, v, k, data in G_proj.edges(keys=True, data=True):
        data["health"] = 1.0
        data["blocked"] = False

    print(f"  Projected: {G_proj.number_of_nodes()} nodes, {G_proj.number_of_edges()} edges")

    # ── building footprints ───────────────────────────────────────────────────
    print("Pulling building footprints from OSM...")
    buildings = ox.features_from_point(
        INDIRANAGAR_CENTER,
        tags={"building": True},
        dist=RADIUS_M
    )
    buildings = buildings.to_crs(CRS)
    buildings = buildings[
        buildings.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    ].copy()

    # classify facility types from OSM tags
    def classify_facility(row):
        amenity   = str(row.get("amenity", "")).lower()
        building  = str(row.get("building", "")).lower()
        healthcare = str(row.get("healthcare", "")).lower()
        emergency = str(row.get("emergency", "")).lower()

        if amenity in ["hospital", "clinic"] or healthcare == "hospital" or building == "hospital":
            return "hospital"
        elif amenity == "fire_station" or emergency == "fire_station":
            return "fire_station"
        elif amenity == "police":
            return "police"
        elif amenity in ["school", "university", "college"]:
            return "school"
        elif building in ["industrial", "warehouse"]:
            return "industrial"
        else:
            return "general"

    buildings["facility_type"] = buildings.apply(classify_facility, axis=1)
    buildings["criticality_weight"] = buildings["facility_type"].map({
        "hospital":    10.0,
        "fire_station": 8.0,
        "police":       7.0,
        "school":       4.0,
        "industrial":   3.0,
        "general":      1.0
    })

    critical = buildings[buildings["facility_type"] != "general"]
    print(f"  Buildings: {len(buildings)} total, {len(critical)} critical")
    print(f"  Types: {critical['facility_type'].value_counts().to_dict()}")

    # ── save ──────────────────────────────────────────────────────────────────
    ox.save_graphml(G_proj, filepath=f"{OUTPUT_DIR}/road_graph.graphml")
    nodes_gdf, edges_gdf = ox.graph_to_gdfs(G_proj)
    nodes_gdf.to_file(f"{OUTPUT_DIR}/road_nodes.gpkg", driver="GPKG")
    edges_gdf.to_file(f"{OUTPUT_DIR}/road_edges.gpkg", driver="GPKG")
    buildings.to_file(f"{DATA_DIR}/buildings.gpkg", driver="GPKG")

    print(f"  Saved: {OUTPUT_DIR}/road_graph.graphml")
    print(f"  Saved: {OUTPUT_DIR}/road_nodes.gpkg + road_edges.gpkg")
    print(f"  Saved: {DATA_DIR}/buildings.gpkg")

    return G_proj, buildings


if __name__ == "__main__":
    G_road, buildings = build_road_graph()

    print("\nSanity checks:")
    print(f"  Weakly connected: {nx.is_weakly_connected(G_road)}")
    largest = len(max(nx.weakly_connected_components(G_road), key=len))
    print(f"  Largest component: {largest} nodes")
    sample_node = list(G_road.nodes(data=True))[0]
    print(f"  Sample node attrs: {list(sample_node[1].keys())}")
    print("\nStep 1 complete.")
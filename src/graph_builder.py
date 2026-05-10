"""
Phase 1 - Step 1: Road graph + building footprints
Pulls real Halasuru, Bengaluru data from OpenStreetMap via OSMnx
"""

import osmnx as ox
import geopandas as gpd
import networkx as nx
import os

# ── config ────────────────────────────────────────────────────────────────────
HALASURU_CENTER = (12.9762, 77.6265)  # Halasuru metro station
RADIUS_M        = 2000                # 2 km radius
CRS             = "EPSG:32643"        # UTM zone 43N for Bengaluru
OUTPUT_DIR      = "graphs"
DATA_DIR        = "data"

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(DATA_DIR,   exist_ok=True)

# ── OSM feature tags to pull ──────────────────────────────────────────────────
FEATURE_TAGS = {
    "building": [
        # residential (collapse into one category)
        "apartments", "hotel", "residential",
        # civic / government
        "civic", "government", "public",
        # commercial / economic
        "commercial", "office", "supermarket", "warehouse", "industrial",
    ],
    "amenity": [
        # emergency / safety
        "hospital", "clinic", "ambulance_station",
        "fire_station", "police",
        "shelter",
        # civic / government
        "courthouse", "prison", "community_centre",
        "college", "university", "school", "kindergarten",
        # supply chain
        "fuel", "marketplace",
    ],
    "office": ["government"],
}

# ── criticality weights ───────────────────────────────────────────────────────
# Higher = more critical to network functioning and cascade propagation
FACILITY_WEIGHTS = {
    "hospital":           10.0,
    "ambulance_station":   9.0,
    "fire_station":        8.0,
    "police":              7.0,
    "government":          6.0,  # offices + courthouse + prison
    "fuel":                6.0,  # loss of power → loss of fuel distribution
    "shelter":             5.0,
    "school":              4.0,  # school + college + university + kindergarten
    "community_centre":    4.0,
    "marketplace":         4.0,
    "industrial":          3.0,
    "warehouse":           3.0,
    "commercial":          2.0,
    "residential":         1.5,  # population exposure weight
    "general":             1.0,
}


def classify_facility(row):
    amenity   = str(row.get("amenity",    "")).lower().strip()
    building  = str(row.get("building",   "")).lower().strip()
    office    = str(row.get("office",     "")).lower().strip()
    healthcare = str(row.get("healthcare","")).lower().strip()
    emergency = str(row.get("emergency",  "")).lower().strip()

    # ── emergency / health ────────────────────────────────────────────────────
    if amenity in ("hospital", "clinic") or healthcare == "hospital" or building == "hospital":
        return "hospital"
    if amenity == "ambulance_station":
        return "ambulance_station"
    if amenity == "fire_station" or emergency == "fire_station":
        return "fire_station"
    if amenity == "police":
        return "police"

    # ── government / civic ────────────────────────────────────────────────────
    if (office == "government"
            or amenity in ("courthouse", "prison")
            or building in ("government", "civic", "public")):
        return "government"

    # ── supply / energy ───────────────────────────────────────────────────────
    if amenity == "fuel":
        return "fuel"
    if amenity == "marketplace":
        return "marketplace"

    # ── community / social ────────────────────────────────────────────────────
    if amenity == "community_centre":
        return "community_centre"
    if amenity == "shelter":
        return "shelter"

    # ── education ─────────────────────────────────────────────────────────────
    if amenity in ("school", "college", "university", "kindergarten"):
        return "school"

    # ── industrial / storage ──────────────────────────────────────────────────
    if building in ("industrial", "warehouse"):
        return "industrial" if building == "industrial" else "warehouse"

    # ── commercial ────────────────────────────────────────────────────────────
    if building in ("commercial", "office", "supermarket"):
        return "commercial"

    # ── residential (collapsed) ───────────────────────────────────────────────
    if building in ("apartments", "hotel", "residential"):
        return "residential"

    return "general"


def build_road_graph():
    print("\n=== STEP 1: Road graph + buildings ===")

    # ── road graph ────────────────────────────────────────────────────────────
    print("Pulling road graph from OpenStreetMap...")
    G = ox.graph_from_point(
        HALASURU_CENTER,
        dist=RADIUS_M,
        network_type="drive",
        simplify=True,
        retain_all=False
    )
    print(f"  Raw graph     : {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    G_proj = ox.project_graph(G, to_crs=CRS)
    G_proj = ox.add_edge_speeds(G_proj)
    G_proj = ox.add_edge_travel_times(G_proj)

    # node simulation attributes
    for node_id, data in G_proj.nodes(data=True):
        data["health"]       = 1.0
        data["network"]      = "road"
        data["criticality"]  = 1.0   # updated after building snap below
        data["flood_risk"]   = False
        data["pop_density"]  = 1.0
        data["elevation"]    = 0.0   # NOTE: placeholder — replace with SRTM/Copernicus DEM

    # edge simulation attributes
    for u,v,k, data in G_proj.edges(keys=True, data=True):
        data["health"]         = 1.0
        data["blocked"]        = False
        data["highway_class"]  = data.get("highway",  "unclassified")
        data["is_bridge"]      = data.get("bridge",   False)
        data["is_tunnel"]      = data.get("tunnel",   False)
        data["surface"]        = data.get("surface",  "unknown")
        data["height_restriction"] = data.get("maxheight", None)

    print(f"  Projected     : {G_proj.number_of_nodes()} nodes, {G_proj.number_of_edges()} edges")

    # ── building footprints ───────────────────────────────────────────────────
    print("Pulling building footprints from OSM...")
    buildings = ox.features_from_point(
        HALASURU_CENTER,
        tags=FEATURE_TAGS,
        dist=RADIUS_M
    )
    buildings = buildings.to_crs(CRS)
    buildings = buildings[
        buildings.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    ].copy()

    buildings["facility_type"]      = buildings.apply(classify_facility, axis=1)
    buildings["criticality_weight"] = buildings["facility_type"].map(FACILITY_WEIGHTS).fillna(1.0)

    critical = buildings[buildings["facility_type"] != "general"]
    print(f"  Buildings     : {len(buildings)} total, {len(critical)} critical")
    print(f"  Types         : {critical['facility_type'].value_counts().to_dict()}")

    # ── snap buildings → nearest road node, propagate max criticality ─────────
    print("Snapping buildings to nearest road nodes...")
    buildings["nearest_node"] = buildings.geometry.centroid.apply(
        lambda geom: ox.nearest_nodes(G_proj, geom.x, geom.y)
    )
    node_criticality = buildings.groupby("nearest_node")["criticality_weight"].max()
    for node_id, crit in node_criticality.items():
        if node_id in G_proj.nodes:
            G_proj.nodes[node_id]["criticality"] = crit
    print(f"  Nodes updated : {len(node_criticality)} road nodes received building criticality")

    # ── save ──────────────────────────────────────────────────────────────────
    ox.save_graphml(G_proj, filepath=f"{OUTPUT_DIR}/road_graph.graphml")
    nodes_gdf, edges_gdf = ox.graph_to_gdfs(G_proj)
    nodes_gdf.to_file(f"{OUTPUT_DIR}/road_nodes.gpkg", driver="GPKG")
    edges_gdf.to_file(f"{OUTPUT_DIR}/road_edges.gpkg", driver="GPKG")
    buildings.to_file(f"{DATA_DIR}/buildings.gpkg",    driver="GPKG")

    print(f"  Saved: {OUTPUT_DIR}/road_graph.graphml")
    print(f"  Saved: {OUTPUT_DIR}/road_nodes.gpkg + road_edges.gpkg")
    print(f"  Saved: {DATA_DIR}/buildings.gpkg")

    return G_proj, buildings


if __name__ == "__main__":
    G_road, buildings = build_road_graph()

    print("\nSanity checks:")
    print(f"  Weakly connected : {nx.is_weakly_connected(G_road)}")
    largest = len(max(nx.weakly_connected_components(G_road), key=len))
    print(f"  Largest component: {largest} nodes")
    sample_node = list(G_road.nodes(data=True))[0]
    print(f"  Sample node attrs: {list(sample_node[1].keys())}")
    non_default = sum(
        1 for _, d in G_road.nodes(data=True) if d["criticality"] > 1.0
    )
    print(f"  Nodes with elevated criticality: {non_default}")
    print("\nStep 1 complete.")
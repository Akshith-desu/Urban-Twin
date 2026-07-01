
"""
power.py
Builds the power infrastructure graph for Halasuru, Bengaluru.

Steps:
  1. Query OSMnx for substations, transformers, generators, plants
  2. Assign node attributes (synthetic values from ranges below)
  3. Connect buildings → nearest transformer (road-routed)
  4. Connect transformers → nearest + backup substation (road-routed)
  5. Save power_nodes.gpkg, power_edges.gpkg, power.json
  6. Display using matplotlib/geopandas
"""

import os
import json
import random
import osmnx as ox
import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import networkx as nx

from shapely.geometry import LineString
from scipy.spatial   import cKDTree


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION — all hardcoded values and ranges live here only
# ══════════════════════════════════════════════════════════════════════════════

CENTER          = (12.9762, 77.6265)    # Halasuru, Bengaluru
RADIUS_M        = 2000
CRS             = "EPSG:32643"          # UTM zone 43N — metres
OUTPUT_DIR      = "graphs"
DATA_DIR        = "data"
BUILDINGS_PATH  = f"{DATA_DIR}/buildings.gpkg"
ROAD_GRAPH_PATH = f"{OUTPUT_DIR}/road_graph.graphml"

# ── OSM query tags ────────────────────────────────────────────────────────────
POWER_TAGS = {
    "power": ["substation", "transformer", "generator", "plant"]
}

# ── substation synthetic attribute ranges ─────────────────────────────────────
# Source: BESCOM 11kV urban substation specs, IEEE-RTS-96
SUBSTATION_RATED_CAPACITY_MW_RANGE   = (5.0,  20.0)
SUBSTATION_VOLTAGE_KV                = 11.0              # BESCOM distribution standard
SUBSTATION_IMPEDANCE_OHM_RANGE       = (0.1,  0.5)      # typical 11kV busbar
SUBSTATION_RELAY_TMS_RANGE           = (0.1,  0.3)      # CEA protection guidelines

# ── transformer synthetic attribute ranges ────────────────────────────────────
# Source: BESCOM DTC standard capacities
TRANSFORMER_CAPACITIES_KVA           = [25, 63, 100, 250, 500]   # kVA → MW on assignment
TRANSFORMER_VOLTAGE_KV               = 0.415             # secondary side, 3-phase
TRANSFORMER_IMPEDANCE_OHM_RANGE      = (0.01, 0.05)     # winding impedance at 415V
TRANSFORMER_POWER_FACTOR             = 0.85              # BESCOM standard
TRANSFORMER_THERMAL_LIMIT_C          = 105.0             # IEC 60076-2 ONAN class
TRANSFORMER_THERMAL_TIME_CONST_RANGE = (65.0, 150.0)    # IEC 60076-7
TRANSFORMER_COOLING_TYPES            = ["ONAN", "ONAF"]
TRANSFORMER_INIT_TEMP_C_RANGE        = (25.0, 45.0)     # ambient + initial load

# ── 11kV feeder line (primary/backup supply) synthetic attribute ranges ───────
# Source: ACSR conductor specs, CEA guidelines
FEEDER_RATED_CAPACITY_MW_RANGE       = (2.0,  8.0)
FEEDER_IMPEDANCE_OHM_PER_KM_RANGE    = (0.3,  0.5)
FEEDER_RESISTANCE_OHM_PER_KM_RANGE   = (0.25, 0.4)
FEEDER_THERMAL_LIMIT_C_RANGE         = (75.0, 90.0)     # overhead ACSR conductor
FEEDER_THERMAL_TIME_CONST_RANGE      = (10.0, 20.0)     # overhead line heats faster
FEEDER_COOLING_CAPACITY_RANGE        = (0.02, 0.05)     # convection coefficient
FEEDER_RELAY_TMS_RANGE               = (0.1,  0.3)      # CEA protection guidelines
FEEDER_LINE_TYPES                    = ["overhead", "underground"]
FEEDER_INIT_TEMP_C_RANGE             = (25.0, 40.0)

# ── low voltage edge (building service drop / road feeder) ranges ─────────────
# Source: IEC 60898 MCB, IEC 60947-2 MCCB ratings
LV_VOLTAGE_OPTIONS                   = [230, 415]        # single phase / three phase
LV_RATED_CURRENT_A_RANGE             = (16.0, 100.0)    # MCB/MCCB range

# ── display colours ───────────────────────────────────────────────────────────
NODE_COLOURS = {
    "substation":  "#FFC0CB",
    "transformer": "#f97316",
    "generator":   "#22c55e",
    "plant":       "#dc2626",
    "default":     "#a1a1aa",
}
EDGE_COLOURS = {
    "building_service_drop": "#7dd3fc",
    "road_feeder":           "#0ea5e9",
    "primary_supply":        "#facc15",
    "backup_supply":         "#a78bfa",
    "default":               "#d4d4d4",
}

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(DATA_DIR,   exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def road_path_geometry(G_road, road_node_u, road_node_v):
    """Shortest path along road graph → (LineString, length_m)."""
    try:
        path = nx.shortest_path(G_road, road_node_u, road_node_v, weight="length")
        coords = [(G_road.nodes[n]["x"], G_road.nodes[n]["y"]) for n in path]
        if len(coords) < 2:
            return None, 0
        length = sum(
            G_road.edges[path[i], path[i+1], 0].get("length", 0)
            for i in range(len(path) - 1)
        )
        return LineString(coords), length
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None, None


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — QUERY OSM POWER NODES
# ══════════════════════════════════════════════════════════════════════════════

def query_power_nodes():
    print("Querying OSM power infrastructure...")
    features = ox.features_from_point(CENTER, tags=POWER_TAGS, dist=RADIUS_M)
    features = features.to_crs(CRS)

    nodes = features[
        features.geometry.geom_type.isin(["Point", "Polygon", "MultiPolygon"])
    ].copy()

    nodes["geometry"] = nodes.geometry.apply(
        lambda g: g.centroid if g.geom_type in ["Polygon", "MultiPolygon"] else g
    )
    nodes = nodes.reset_index(drop=True)
    print(f"  Features returned: {len(nodes)}")
    if "power" in nodes.columns:
        print(f"  Breakdown:\n{nodes['power'].value_counts().to_string()}")
    return nodes


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — BUILD NODE ATTRIBUTES
# ══════════════════════════════════════════════════════════════════════════════

def build_node_attributes(nodes):
    """
    Assign node_id, name, lat/lon, x/y, and physics attributes.
    All synthetic values drawn from ranges defined in CONFIGURATION.
    node_id is set as the dataframe index so gpkg row ID matches JSON node_id.
    """
    type_counters = {"substation": 0, "transformer": 0, "generator": 0, "plant": 0}

    def make_name(node_type):
        if node_type not in type_counters:
            return f"Unknown {node_type}"
        type_counters[node_type] += 1
        return f"{node_type.title()} {type_counters[node_type]}"

    # ── core identity ─────────────────────────────────────────────────────────
    nodes["node_id"]   = nodes.index.astype(str)
    nodes["name"]      = nodes["power"].apply(make_name)
    nodes_wgs84        = nodes.to_crs("EPSG:4326")
    nodes["latitude"]  = nodes_wgs84.geometry.y
    nodes["longitude"] = nodes_wgs84.geometry.x
    nodes["x"]         = nodes.geometry.x
    nodes["y"]         = nodes.geometry.y

    # ── physics attributes — one random draw per node ─────────────────────────
    rated_capacity_list      = []
    voltage_kv_list          = []
    impedance_ohm_list       = []
    relay_TMS_list           = []
    power_factor_list        = []
    thermal_limit_c_list     = []
    thermal_time_const_list  = []
    cooling_type_list        = []
    init_temp_c_list         = []
    health_list              = []
    operational_status_list  = []
    load_fraction_list       = []

    for _, row in nodes.iterrows():
        ntype = str(row.get("power", "")).lower().strip()

        if ntype == "substation":
            rated_capacity_list.append(
                round(random.uniform(*SUBSTATION_RATED_CAPACITY_MW_RANGE), 2))
            voltage_kv_list.append(SUBSTATION_VOLTAGE_KV)
            impedance_ohm_list.append(
                round(random.uniform(*SUBSTATION_IMPEDANCE_OHM_RANGE), 4))
            relay_TMS_list.append(
                round(random.uniform(*SUBSTATION_RELAY_TMS_RANGE), 3))
            power_factor_list.append(None)
            thermal_limit_c_list.append(None)
            thermal_time_const_list.append(None)
            cooling_type_list.append(None)
            init_temp_c_list.append(None)

        elif ntype == "transformer":
            capacity_kva = random.choice(TRANSFORMER_CAPACITIES_KVA)
            rated_capacity_list.append(round(capacity_kva / 1000, 4))
            voltage_kv_list.append(TRANSFORMER_VOLTAGE_KV)
            impedance_ohm_list.append(
                round(random.uniform(*TRANSFORMER_IMPEDANCE_OHM_RANGE), 5))
            relay_TMS_list.append(None)
            power_factor_list.append(TRANSFORMER_POWER_FACTOR)
            thermal_limit_c_list.append(TRANSFORMER_THERMAL_LIMIT_C)
            thermal_time_const_list.append(
                round(random.uniform(*TRANSFORMER_THERMAL_TIME_CONST_RANGE), 1))
            cooling_type_list.append(random.choice(TRANSFORMER_COOLING_TYPES))
            init_temp_c_list.append(
                round(random.uniform(*TRANSFORMER_INIT_TEMP_C_RANGE), 1))

        else:
            # generator, plant, unknown — use substation ranges
            rated_capacity_list.append(
                round(random.uniform(*SUBSTATION_RATED_CAPACITY_MW_RANGE), 2))
            voltage_kv_list.append(SUBSTATION_VOLTAGE_KV)
            impedance_ohm_list.append(
                round(random.uniform(*SUBSTATION_IMPEDANCE_OHM_RANGE), 4))
            relay_TMS_list.append(
                round(random.uniform(*SUBSTATION_RELAY_TMS_RANGE), 3))
            power_factor_list.append(None)
            thermal_limit_c_list.append(None)
            thermal_time_const_list.append(None)
            cooling_type_list.append(None)
            init_temp_c_list.append(None)

        health_list.append(1.0)
        operational_status_list.append("normal")
        load_fraction_list.append(round(random.uniform(0.4, 0.8), 3))

    nodes["rated_capacity_mw"]         = rated_capacity_list
    nodes["voltage_kv"]                = voltage_kv_list
    nodes["impedance_ohm"]             = impedance_ohm_list
    nodes["relay_TMS"]                 = relay_TMS_list
    nodes["power_factor"]              = power_factor_list
    nodes["thermal_limit_c"]           = thermal_limit_c_list
    nodes["thermal_time_constant_min"] = thermal_time_const_list
    nodes["cooling_type"]              = cooling_type_list
    nodes["current_temperature_c"]     = init_temp_c_list
    nodes["health"]                    = health_list
    nodes["operational_status"]        = operational_status_list
    nodes["load_fraction"]             = load_fraction_list

    # transformer interdependency — filled after edges are built
    nodes["substation_supplying"]                = None
    nodes["second_nearest_substation_supplying"] = None

    # set node_id as index — gpkg row ID now matches JSON node_id
    nodes = nodes.reset_index(drop=True)

    return nodes


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — BUILDING → TRANSFORMER EDGES
# ══════════════════════════════════════════════════════════════════════════════

def build_building_transformer_edges(buildings, transformer_nodes, G_road):
    """
    For each building:
      building_service_drop : centroid → nearest road node (stub)
      road_feeder           : nearest road node → transformer road node (road-routed)
    edge_id starts at 1 and increments continuously.
    """
    print("Building → transformer edges (road-routed)...")

    transformer_coords = np.array([
        [row.geometry.x, row.geometry.y]
        for _, row in transformer_nodes.iterrows()
    ])
    transformer_names  = list(transformer_nodes["name"])
    tree               = cKDTree(transformer_coords)

    edges        = []
    edge_counter = 1
    total        = len(buildings)

    for i, (building_idx, building) in enumerate(buildings.iterrows()):
        if i % 100 == 0:
            print(f"  Processing building {i+1}/{total}...")

        centroid = building.geometry.centroid
        bx, by   = centroid.x, centroid.y

        _, ti            = tree.query([bx, by], k=1)
        transformer_name = transformer_names[ti]
        tx               = transformer_coords[ti][0]
        ty               = transformer_coords[ti][1]

        snap_b  = ox.nearest_nodes(G_road, bx, by)
        snap_t  = ox.nearest_nodes(G_road, tx, ty)
        road_bx = G_road.nodes[snap_b]["x"]
        road_by = G_road.nodes[snap_b]["y"]
        road_tx = G_road.nodes[snap_t]["x"]
        road_ty = G_road.nodes[snap_t]["y"]

        # service drop
        service_geom = LineString([(bx, by), (road_bx, road_by)])
        edges.append({
            "edge_id":         edge_counter,
            "from":            f"Building_{building_idx}",
            "to":              transformer_name,
            "edge_type":       "building_service_drop",
            "length_m":        round(float(service_geom.length), 2),
            "voltage_v":       random.choice(LV_VOLTAGE_OPTIONS),
            "rated_current_a": round(random.uniform(*LV_RATED_CURRENT_A_RANGE), 1),
            "health":          1.0,
            "blocked":         False,
            "geometry":        service_geom,
        })
        edge_counter += 1

        # road feeder
        feeder_geom, feeder_length = road_path_geometry(G_road, snap_b, snap_t)
        if feeder_geom is None:
            feeder_geom   = LineString([(road_bx, road_by), (road_tx, road_ty)])
            feeder_length = feeder_geom.length

        edges.append({
            "edge_id":         edge_counter,
            "from":            f"Building_{building_idx}",
            "to":              transformer_name,
            "edge_type":       "road_feeder",
            "length_m":        round(float(feeder_length), 2),
            "voltage_v":       random.choice(LV_VOLTAGE_OPTIONS),
            "rated_current_a": round(random.uniform(*LV_RATED_CURRENT_A_RANGE), 1),
            "health":          1.0,
            "blocked":         False,
            "geometry":        feeder_geom,
        })
        edge_counter += 1

    return gpd.GeoDataFrame(edges, crs=CRS), edge_counter


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — TRANSFORMER → SUBSTATION EDGES
# ══════════════════════════════════════════════════════════════════════════════

def build_transformer_substation_edges(
    transformer_nodes, substation_nodes, G_road, edge_counter_start
):
    """
    For each transformer:
      primary_supply : transformer → nearest substation (road-routed)
      backup_supply  : transformer → second nearest substation (road-routed)
    edge_id continues from edge_counter_start.
    """
    print("Transformer → substation edges (road-routed)...")

    substation_coords = np.array([
        [row.geometry.x, row.geometry.y]
        for _, row in substation_nodes.iterrows()
    ])
    substation_names  = list(substation_nodes["name"])
    tree              = cKDTree(substation_coords)

    edges        = []
    edge_counter = edge_counter_start

    for _, transformer in transformer_nodes.iterrows():
        tx     = transformer.geometry.x
        ty     = transformer.geometry.y
        k      = min(2, len(substation_names))
        _, sis = tree.query([tx, ty], k=k)

        if k == 1:
            sis = [sis]

        for rank, si in enumerate(sis):
            sub_name  = substation_names[si]
            sx        = substation_coords[si][0]
            sy        = substation_coords[si][1]

            snap_t        = ox.nearest_nodes(G_road, tx, ty)
            snap_s        = ox.nearest_nodes(G_road, sx, sy)
            geom, length  = road_path_geometry(G_road, snap_t, snap_s)

            if geom is None:
                geom   = LineString([(tx, ty), (sx, sy)])
                length = geom.length

            length_km = float(length) / 1000.0

            edges.append({
                "edge_id":                   edge_counter,
                "from":                      transformer["name"],
                "to":                        sub_name,
                "edge_type":                 "primary_supply" if rank == 0 else "backup_supply",
                "length_m":                  round(float(length), 2),
                # 11kV feeder physics — one random draw per edge
                "rated_capacity_mw":         round(random.uniform(*FEEDER_RATED_CAPACITY_MW_RANGE), 2),
                "impedance_ohm":             round(
                    random.uniform(*FEEDER_IMPEDANCE_OHM_PER_KM_RANGE) * length_km, 4),
                "resistance_ohm":            round(
                    random.uniform(*FEEDER_RESISTANCE_OHM_PER_KM_RANGE) * length_km, 4),
                "thermal_limit_c":           round(random.uniform(*FEEDER_THERMAL_LIMIT_C_RANGE), 1),
                "thermal_time_constant_min": round(random.uniform(*FEEDER_THERMAL_TIME_CONST_RANGE), 1),
                "cooling_capacity":          round(random.uniform(*FEEDER_COOLING_CAPACITY_RANGE), 4),
                "relay_TMS":                 round(random.uniform(*FEEDER_RELAY_TMS_RANGE), 3),
                "line_type":                 random.choice(FEEDER_LINE_TYPES),
                # state variables — initial values
                "current_flow_mw":           0.0,
                "current_temperature_c":     round(random.uniform(*FEEDER_INIT_TEMP_C_RANGE), 1),
                "time_overloaded_seconds":   0.0,
                "health":                    1.0,
                "blocked":                   False,
                "geometry":                  geom,
            })
            edge_counter += 1

    return gpd.GeoDataFrame(edges, crs=CRS), edge_counter


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — ENRICH TRANSFORMER NODES WITH SUBSTATION REFERENCES
# ══════════════════════════════════════════════════════════════════════════════

def add_substation_attributes(nodes, transformer_edges):
    primary_map = {}
    backup_map  = {}
    for _, row in transformer_edges.iterrows():
        if row["edge_type"] == "primary_supply":
            primary_map[row["from"]] = row["to"]
        elif row["edge_type"] == "backup_supply":
            backup_map[row["from"]]  = row["to"]
    nodes["substation_supplying"]                = nodes["name"].map(primary_map)
    nodes["second_nearest_substation_supplying"] = nodes["name"].map(backup_map)
    return nodes


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — EXPORT JSON
# ══════════════════════════════════════════════════════════════════════════════

def export_power_json(nodes_gdf, edges_gdf, output_path):
    print("Exporting power.json...")

    node_records = []
    for _, row in nodes_gdf.iterrows():
        node_records.append({
            "node_id":   row.get("node_id"),
            "name":      row.get("name"),
            "power":     row.get("power"),
            "latitude":  row.get("latitude"),
            "longitude": row.get("longitude"),
            "x":         row.get("x"),
            "y":         row.get("y"),
            "rated_capacity_mw":             row.get("rated_capacity_mw"),
            "voltage_kv":                    row.get("voltage_kv"),
            "impedance_ohm":                 row.get("impedance_ohm"),
            "relay_TMS":                     row.get("relay_TMS"),
            "power_factor":                  row.get("power_factor"),
            "thermal_limit_c":               row.get("thermal_limit_c"),
            "thermal_time_constant_min":     row.get("thermal_time_constant_min"),
            "cooling_type":                  row.get("cooling_type"),
            "current_temperature_c":         row.get("current_temperature_c"),
            "health":                        row.get("health"),
            "operational_status":            row.get("operational_status"),
            "load_fraction":                 row.get("load_fraction"),
            "substation_supplying":          row.get("substation_supplying"),
            "second_nearest_substation_supplying":
                row.get("second_nearest_substation_supplying"),
        })

    edge_records = []
    for _, row in edges_gdf.iterrows():
        record = {
            "edge_id":   int(row.get("edge_id")),
            "from":      row.get("from"),
            "to":        row.get("to"),
            "edge_type": row.get("edge_type"),
            "length_m":  row.get("length_m"),
            "health":    row.get("health"),
            "blocked":   row.get("blocked"),
        }
        for attr in [
            "rated_capacity_mw", "impedance_ohm", "resistance_ohm",
            "thermal_limit_c", "thermal_time_constant_min",
            "cooling_capacity", "relay_TMS", "line_type",
            "current_flow_mw", "current_temperature_c",
            "time_overloaded_seconds", "voltage_v", "rated_current_a",
        ]:
            val = row.get(attr)
            if val is not None:
                record[attr] = val
        edge_records.append(record)

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

# query + attributes
nodes = query_power_nodes()
nodes = build_node_attributes(nodes)

# split by type
transformer_nodes = nodes[nodes["power"] == "transformer"].copy()
substation_nodes  = nodes[nodes["power"] == "substation"].copy()
print(f"  Transformers: {len(transformer_nodes)}")
print(f"  Substations:  {len(substation_nodes)}")

# build edges
building_edges, next_counter = build_building_transformer_edges(
    buildings, transformer_nodes, G_road
)
transformer_edges, _ = build_transformer_substation_edges(
    transformer_nodes, substation_nodes, G_road, next_counter
)

# enrich nodes
nodes = add_substation_attributes(nodes, transformer_edges)

# merge edges
power_edges = gpd.GeoDataFrame(
    pd.concat([building_edges, transformer_edges], ignore_index=True),
    crs=CRS
)

# set edge_id as index — gpkg row ID now matches JSON edge_id
power_edges = power_edges.reset_index(drop=True)

print(f"  Total edges: {len(power_edges)}")

# save
nodes_path = f"{OUTPUT_DIR}/power_nodes.gpkg"
edges_path = f"{OUTPUT_DIR}/power_edges.gpkg"
json_path  = f"{OUTPUT_DIR}/power.json"

nodes.to_file(nodes_path,       driver="GPKG")
power_edges.to_file(edges_path, driver="GPKG")
export_power_json(nodes, power_edges, json_path)

print(f"Saved: {nodes_path}")
print(f"Saved: {edges_path}")
print(f"Saved: {json_path}")


# ══════════════════════════════════════════════════════════════════════════════
# DISPLAY
# ══════════════════════════════════════════════════════════════════════════════

fig, ax = plt.subplots(figsize=(15, 15))
ax.set_facecolor("#1a1a2e")
fig.patch.set_facecolor("#1a1a2e")

for edge_type, colour in EDGE_COLOURS.items():
    subset = power_edges[power_edges["edge_type"] == edge_type]
    if len(subset) == 0:
        continue
    linewidth = {
        "primary_supply":        2.5,
        "backup_supply":         2.0,
        "road_feeder":           0.9,
        "building_service_drop": 0.4,
    }.get(edge_type, 1.0)
    subset.plot(ax=ax, color=colour, linewidth=linewidth,
                alpha=0.75, label=edge_type, zorder=2)

for node_type, colour in NODE_COLOURS.items():
    subset = nodes[nodes.get("power", pd.Series(dtype=str)) == node_type]
    if len(subset) == 0:
        continue
    size = {"substation": 140, "transformer": 70}.get(node_type, 40)
    subset.plot(ax=ax, color=colour, markersize=size,
                alpha=0.95, label=node_type, zorder=5)

ax.legend(loc="lower left", facecolor="#0f0f23", edgecolor="#444",
          labelcolor="white", fontsize=9)
ax.set_title("Power Infrastructure — Halasuru, Bengaluru",
             color="white", fontsize=16, pad=12)
ax.tick_params(colors="white")
for spine in ax.spines.values():
    spine.set_edgecolor("#444")

plt.tight_layout()
plt.show()

# Cascade Logic and Equations — Full Restatement
# Trigger: Substation S1 fails (health = 0, flood or fault)


# Step 1 — DC Power Flow recomputes:
# P_flow = (V_i - V_j) / Z_ij
# All transformers that were on S1's primary supply now reroute to their second_nearest_substation_supplying. The backup substation S2 now carries additional load.


# Step 2 — New total load on S2:
# I_total_S2 = Σ(P_flow_each_transformer / (√3 × V_kv × power_factor))
# load_fraction_S2 = I_total_S2 / I_rated_S2


# Step 3 — Check each feeder line from S2 to its transformers (IEC 60255-151 Standard Inverse):
# I_feeder = P_flow_transformer / (√3 × V_kv × power_factor)
# if I_feeder > I_rated_feeder:
#     t_trip = TMS × (0.14 / ((I_feeder / I_rated_feeder)^0.02 - 1))
#     time_overloaded += tick_duration_seconds
#     if time_overloaded >= t_trip:
#         feeder_edge.health = 0
#         feeder_edge.blocked = True
#         transformer loses supply entirely


# Step 4 — Transformer loses supply, buildings go dark:
# if transformer.health == 0:
#     all buildings connected to this transformer → power_lost = True
#     cell towers connected → switch to battery backup
#     traffic signals connected → switch to UPS


# Step 5 — Joule heating on feeder lines (runs in parallel with Step 3):
# P_heat = I_feeder² × R_line
# dT/dt = (P_heat - cooling_capacity × (T_current - T_ambient)) / thermal_time_constant
# T_new = T_current + dT/dt × tick_duration_minutes


# Step 6 — Line fails from thermal overload if relay hasn't tripped yet:
# if T_new > T_rated_line:
#     line.health degrades
#     if line.health < 0.1:
#         line.blocked = True
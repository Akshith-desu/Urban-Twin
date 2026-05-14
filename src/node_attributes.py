"""
Phase 1 - Step 4: Node attributes + flood zone join + validation
- Assign population density weights to all nodes
- Join BBMP flood zone shapefile (or synthetic flood polygon)
- Compute final criticality scores
- Run validation and produce summary report
"""

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import osmnx as ox
import os
import json
from shapely.geometry import Point, Polygon
from pyproj import Transformer

INDIRANAGAR_CENTER = (12.9784, 77.6408)
RADIUS_M = 1500
CRS = "EPSG:32643"
OUTPUT_DIR = "graphs"
DATA_DIR = "data"


# ══════════════════════════════════════════════════════════════════════════════
# FLOOD ZONE
# ══════════════════════════════════════════════════════════════════════════════

def load_or_create_flood_zones():
    """
    Load BBMP flood zone shapefile if available.
    Falls back to a synthetic flood polygon around Ulsoor Lake
    (historically flood-prone area in Bengaluru).
    """
    print("\n=== STEP 4A: Flood zones ===")

    # Check if real shapefile exists (user can place it at data/flood_zones.gpkg)
    flood_path = f"{DATA_DIR}/flood_zones.gpkg"
    if os.path.exists(flood_path):
        flood_zones = gpd.read_file(flood_path)
        flood_zones = flood_zones.to_crs(CRS)
        print(f"  Loaded real flood zones: {len(flood_zones)} polygons")
        return flood_zones

    # Synthetic flood zone: Ulsoor Lake overflow area
    # Coordinates approximate the low-lying areas around Ulsoor Lake
    print("  No BBMP shapefile found — creating synthetic flood zone")
    print("  (Place real BBMP shapefile at data/flood_zones.gpkg to use real data)")

    transformer = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)

    def ll_to_proj(lon, lat):
        return transformer.transform(lon, lat)

    # Ulsoor Lake flood zone (low-lying areas that flood in heavy monsoon)
    ulsoor_coords = [
        ll_to_proj(77.6150, 12.9740),
        ll_to_proj(77.6250, 12.9750),
        ll_to_proj(77.6280, 12.9820),
        ll_to_proj(77.6220, 12.9870),
        ll_to_proj(77.6140, 12.9850),
        ll_to_proj(77.6100, 12.9790),
        ll_to_proj(77.6150, 12.9740),
    ]

    # Secondary flood zone: low-lying area near Domlur flyover
    domlur_coords = [
        ll_to_proj(77.6300, 12.9600),
        ll_to_proj(77.6380, 12.9610),
        ll_to_proj(77.6400, 12.9660),
        ll_to_proj(77.6310, 12.9670),
        ll_to_proj(77.6280, 12.9630),
        ll_to_proj(77.6300, 12.9600),
    ]

    flood_polygons = [
        {"geometry": Polygon(ulsoor_coords), "zone_name": "Ulsoor Lake overflow", "severity": "high"},
        {"geometry": Polygon(domlur_coords), "zone_name": "Domlur low-lying",     "severity": "medium"},
    ]

    flood_zones = gpd.GeoDataFrame(flood_polygons, crs=CRS)
    flood_zones.to_file(flood_path, driver="GPKG")
    print(f"  Created {len(flood_zones)} synthetic flood zones")
    print(f"  Saved: {flood_path}")

    return flood_zones


# ══════════════════════════════════════════════════════════════════════════════
# NODE ATTRIBUTES: FLOOD RISK + CRITICALITY + POP DENSITY
# ══════════════════════════════════════════════════════════════════════════════

def assign_node_attributes(G, network_name, flood_zones, buildings_gdf=None):
    """
    For every node in graph G:
    1. Mark flood_risk=True if node falls within any flood polygon
    2. Compute pop_density weight from building proximity
    3. Recompute final criticality score combining base + pop + facility weights
    """
    print(f"\n  Processing {network_name} nodes...")

    flood_union = flood_zones.geometry.unary_union

    nodes_updated = 0
    flooded_count = 0

    for node_id, data in G.nodes(data=True):
        pt = Point(data["x"], data["y"])

        # flood risk
        in_flood_zone = flood_union.contains(pt)
        data["flood_risk"] = bool(in_flood_zone)
        if in_flood_zone:
            flooded_count += 1

        # population density proxy: count buildings within 200m
        if buildings_gdf is not None and len(buildings_gdf) > 0:
            nearby = buildings_gdf[buildings_gdf.geometry.distance(pt) < 200]
            pop_weight = min(1.0 + len(nearby) * 0.05, 5.0)  # cap at 5x
            data["pop_density"] = round(pop_weight, 3)
        else:
            data["pop_density"] = 1.0

        # final criticality = base criticality × pop_density
        # flood_risk nodes get extra criticality (they're more important to monitor)
        base = data.get("criticality", 1.0)
        pop  = data.get("pop_density", 1.0)
        flood_mult = 1.5 if data["flood_risk"] else 1.0
        data["criticality"] = round(base * pop * flood_mult, 3)

        nodes_updated += 1

    print(f"    Nodes updated: {nodes_updated}")
    print(f"    In flood zone: {flooded_count}")
    return G


def enrich_road_with_facilities(G_road, buildings_gdf):
    """
    For road nodes near critical facilities (hospitals, fire stations),
    raise their criticality significantly — these are emergency access routes.
    """
    print("  Enriching road nodes near critical facilities...")

    critical_buildings = buildings_gdf[
        buildings_gdf["facility_type"].isin(["hospital", "fire_station", "police"])
    ].copy()

    if len(critical_buildings) == 0:
        print("    No critical buildings found in area")
        return G_road

    enriched = 0
    for node_id, data in G_road.nodes(data=True):
        pt = Point(data["x"], data["y"])
        nearby_critical = critical_buildings[
            critical_buildings.geometry.distance(pt) < 300
        ]
        if len(nearby_critical) > 0:
            max_weight = nearby_critical["criticality_weight"].max()
            current = data.get("criticality", 1.0)
            data["criticality"] = round(max(current, max_weight * 0.8), 3)
            data["near_critical_facility"] = True
            enriched += 1
        else:
            data["near_critical_facility"] = False

    print(f"    Road nodes near critical facilities: {enriched}")
    return G_road


# ══════════════════════════════════════════════════════════════════════════════
# SAVE ENRICHED GRAPHS
# ══════════════════════════════════════════════════════════════════════════════

def save_enriched_graph(G, network_name):
    """Re-save graph after attribute enrichment."""
    node_records = []
    for nid, data in G.nodes(data=True):
        record = {"node_id": str(nid), "geometry": Point(data["x"], data["y"])}
        for k, v in data.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                record[k] = v
        node_records.append(record)

    nodes_gdf = gpd.GeoDataFrame(node_records, crs=CRS)
    out_path = f"{OUTPUT_DIR}/{network_name}_nodes_enriched.gpkg"
    nodes_gdf.to_file(out_path, driver="GPKG")
    print(f"  Saved enriched: {out_path}")
    return nodes_gdf


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def validate_all_graphs(G_road, G_power, G_water, G_telecom, dependencies):
    """
    Run sanity checks on all 4 graphs and the dependency registry.
    Prints a pass/fail report.
    """
    print("\n=== STEP 4C: Validation ===")
    results = {}

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        icon = "✓" if condition else "✗"
        print(f"  [{icon}] {name}: {status} {detail}")
        results[name] = condition
        return condition

    print("\n  Road graph:")
    check("Road - weakly connected",
          nx.is_weakly_connected(G_road))
    check("Road - node count > 100",
          G_road.number_of_nodes() > 100,
          f"({G_road.number_of_nodes()} nodes)")
    check("Road - health attribute present",
          all("health" in d for _, d in G_road.nodes(data=True)))
    check("Road - flood_risk attribute present",
          all("flood_risk" in d for _, d in G_road.nodes(data=True)))
    check("Road - criticality attribute present",
          all("criticality" in d for _, d in G_road.nodes(data=True)))

    print("\n  Power graph:")
    check("Power - connected",
          nx.is_connected(G_power),
          f"({G_power.number_of_nodes()} nodes)")
    check("Power - at least 4 substations",
          G_power.number_of_nodes() >= 4)
    check("Power - has edges",
          G_power.number_of_edges() > 0,
          f"({G_power.number_of_edges()} edges)")

    print("\n  Water graph:")
    check("Water - has pump stations",
          any(d.get("node_type") == "pump_station"
              for _, d in G_water.nodes(data=True)))
    check("Water - node count > 10",
          G_water.number_of_nodes() > 10,
          f"({G_water.number_of_nodes()} nodes)")
    check("Water - has edges",
          G_water.number_of_edges() > 0)

    print("\n  Telecom graph:")
    check("Telecom - has towers",
          G_telecom.number_of_nodes() >= 4,
          f"({G_telecom.number_of_nodes()} towers)")
    check("Telecom - MST connected",
          nx.is_connected(G_telecom))

    print("\n  Dependencies:")
    dep_types = set(d["dep_type"] for d in dependencies)
    check("Dependencies - power->water exists",
          "power_supply" in dep_types)
    check("Dependencies - power->road exists",
          "traffic_signal_power" in dep_types)
    check("Dependencies - total > 10",
          len(dependencies) > 10,
          f"({len(dependencies)} total)")

    # summary
    passed = sum(results.values())
    total = len(results)
    print(f"\n  Results: {passed}/{total} checks passed")

    if passed == total:
        print("  Phase 1 validation: ALL PASSED - ready for Phase 2")
    else:
        failed = [k for k, v in results.items() if not v]
        print(f"  Failed checks: {failed}")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY REPORT
# ══════════════════════════════════════════════════════════════════════════════

def generate_summary_report(G_road, G_power, G_water, G_telecom, dependencies, flood_zones):
    """Generate a JSON summary of the entire Phase 1 build."""
    print("\n=== STEP 4D: Summary report ===")

    flood_road = sum(1 for _, d in G_road.nodes(data=True) if d.get("flood_risk"))
    flood_power = sum(1 for _, d in G_power.nodes(data=True) if d.get("flood_risk"))

    # most critical road nodes (top 5)
    road_by_crit = sorted(
        G_road.nodes(data=True),
        key=lambda x: x[1].get("criticality", 0),
        reverse=True
    )[:5]

    report = {
        "phase": 1,
        "location": "Indiranagar, Bengaluru",
        "center_lat": INDIRANAGAR_CENTER[0],
        "center_lon": INDIRANAGAR_CENTER[1],
        "radius_m": RADIUS_M,
        "graphs": {
            "road": {
                "nodes": G_road.number_of_nodes(),
                "edges": G_road.number_of_edges(),
                "flood_risk_nodes": flood_road
            },
            "power": {
                "nodes": G_power.number_of_nodes(),
                "edges": G_power.number_of_edges(),
                "flood_risk_nodes": flood_power
            },
            "water": {
                "nodes": G_water.number_of_nodes(),
                "edges": G_water.number_of_edges(),
                "pump_stations": sum(1 for _, d in G_water.nodes(data=True)
                                     if d.get("node_type") == "pump_station")
            },
            "telecom": {
                "nodes": G_telecom.number_of_nodes(),
                "edges": G_telecom.number_of_edges()
            }
        },
        "dependencies": {
            "total": len(dependencies),
            "by_type": pd.DataFrame(dependencies)["dep_type"].value_counts().to_dict()
                        if dependencies else {}
        },
        "flood_zones": len(flood_zones),
        "top_critical_road_nodes": [
            {"node_id": str(nid), "criticality": round(d.get("criticality", 0), 2)}
            for nid, d in road_by_crit
        ]
    }

    report_path = f"{DATA_DIR}/phase1_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"  Saved: {report_path}")
    print("\n  Phase 1 Summary:")
    print(f"    Road:    {report['graphs']['road']['nodes']} nodes, "
          f"{report['graphs']['road']['edges']} edges")
    print(f"    Power:   {report['graphs']['power']['nodes']} nodes")
    print(f"    Water:   {report['graphs']['water']['nodes']} nodes "
          f"({report['graphs']['water']['pump_stations']} pumps)")
    print(f"    Telecom: {report['graphs']['telecom']['nodes']} towers")
    print(f"    Deps:    {report['dependencies']['total']} cross-network dependencies")
    print(f"    Flood:   {report['flood_zones']} zones, "
          f"{flood_road} road nodes at risk")

    return report


# ══════════════════════════════════════════════════════════════════════════════
# MAIN — RUN ALL 4 STEPS IN SEQUENCE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(__file__))

    from graph_builder import build_road_graph
    from power import build_power_graph, build_telecom_graph
    from water_network import build_water_graph, build_dependency_edges

    print("=" * 60)
    print("PHASE 1: Infrastructure Graph Modelling")
    print("Location: Indiranagar, Bengaluru (1.5km radius)")
    print("=" * 60)

    # Step 1
    G_road, buildings = build_road_graph()

    # Step 2
    # Step 2
    G_power, pn, pe = build_power_graph(G_road)       # ← CORRECT
    G_telecom, tn, te = build_telecom_graph(G_road)   # ← CORRECT

    # Step 3
    G_water, wn, we = build_water_graph(G_road)
    dependencies = build_dependency_edges(G_road, G_power, G_water, G_telecom)

    # Step 4A: flood zones
    flood_zones = load_or_create_flood_zones()

    # Step 4B: assign attributes to all graphs
    print("\n=== STEP 4B: Assigning node attributes ===")
    G_road    = assign_node_attributes(G_road,    "road",    flood_zones, buildings)
    G_power   = assign_node_attributes(G_power,   "power",   flood_zones, buildings)
    G_water   = assign_node_attributes(G_water,   "water",   flood_zones, buildings)
    G_telecom = assign_node_attributes(G_telecom, "telecom", flood_zones, buildings)
    G_road    = enrich_road_with_facilities(G_road, buildings)

    # re-save enriched versions
    save_enriched_graph(G_road,    "road")
    save_enriched_graph(G_power,   "power")
    save_enriched_graph(G_water,   "water")
    save_enriched_graph(G_telecom, "telecom")

    # also re-save road graphml with enriched attributes
    ox.save_graphml(G_road, filepath=f"{OUTPUT_DIR}/road_graph_enriched.graphml")

    # Step 4C: validation
    val_results = validate_all_graphs(G_road, G_power, G_water, G_telecom, dependencies)

    # Step 4D: summary report
    report = generate_summary_report(
        G_road, G_power, G_water, G_telecom, dependencies, flood_zones
    )

    print("\n" + "=" * 60)
    print("PHASE 1 COMPLETE")
    print("All graphs saved to graphs/ and data/")
    print("Next: Phase 2 — Async Event Bus (src/event_bus.py)")
    print("=" * 60)
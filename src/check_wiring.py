"""
Dry-run: show which telecom towers fall within WATER_TELECOM_PROXIMITY_M of each pump.
"""
import json, math, os, sys

WATER_TELECOM_PROXIMITY_M = 800

with open("src/graphs/water.json") as f:
    w = json.load(f)
with open("src/graphs/telecom.json") as f:
    t = json.load(f)

pumps  = [n for n in w["nodes"] if n.get("node_type") == "pump_station"
          and n.get("x") is not None]
towers = t.get("nodes", t.get("towers", []))

# project tower lat/lon → UTM (fallback formula)
def project(lat, lon):
    try:
        from pyproj import Transformer
        proj = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)
        return proj.transform(lon, lat)
    except Exception:
        return lon * 111320 * math.cos(math.radians(lat)), lat * 111320

tower_coords = {}
for n in towers:
    nid = str(n["node_id"])
    lat, lon = n.get("latitude"), n.get("longitude")
    if lat is None or lon is None:
        continue
    tower_coords[nid] = (project(lat, lon), n.get("name", nid))

print(f"Pumps: {len(pumps)}  |  Towers with coords: {len(tower_coords)}\n")
total_links = 0
for pump in pumps:
    pid  = str(pump["node_id"])
    px, py = pump["x"], pump["y"]
    nearby = []
    for nid, ((tx, ty), tname) in tower_coords.items():
        d = math.sqrt((tx - px)**2 + (ty - py)**2)
        if d <= WATER_TELECOM_PROXIMITY_M:
            nearby.append((nid, tname, round(d)))
    print(f"Pump {pid} ({pump.get('name')})  →  {len(nearby)} tower(s) within {WATER_TELECOM_PROXIMITY_M}m")
    for nid, nm, d in nearby:
        print(f"   └─ Tower {nid} '{nm}'  ({d} m)")
    total_links += len(nearby)

print(f"\nTotal links: {total_links}")

if total_links == 0:
    # Show distances to the nearest 3 towers for each pump
    print("\n[DEBUG] No links found — showing nearest distances:")
    for pump in pumps[:3]:
        pid  = str(pump["node_id"])
        px, py = pump["x"], pump["y"]
        dists = []
        for nid, ((tx, ty), tname) in tower_coords.items():
            d = math.sqrt((tx - px)**2 + (ty - py)**2)
            dists.append((d, nid, tname))
        dists.sort()
        print(f"  Pump {pid} nearest: {[(round(d), nm) for d, _, nm in dists[:3]]}")

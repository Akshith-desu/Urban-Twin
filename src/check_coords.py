import json

with open("src/graphs/water.json") as f:
    w = json.load(f)

pumps = [n for n in w["nodes"] if n.get("node_type") == "pump_station"]
print(f"Pump stations: {len(pumps)}")
for p in pumps[:5]:
    nid = p.get("node_id")
    x   = p.get("x")
    y   = p.get("y")
    nm  = p.get("name", "?")
    print(f"  {nid} | x={x} y={y} | name={nm}")

print()

with open("src/graphs/telecom.json") as f:
    t = json.load(f)

towers = t.get("nodes", t.get("towers", []))
print(f"Telecom nodes: {len(towers)}")
for n in towers[:5]:
    nid = n.get("node_id")
    lat = n.get("latitude")
    lon = n.get("longitude")
    nm  = n.get("name", "?")
    print(f"  {nid} | lat={lat} lon={lon} | name={nm}")

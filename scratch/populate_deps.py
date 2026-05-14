import json
import math
import pyproj

def distance(lat1, lon1, lat2, lon2):
    return math.sqrt((lat1 - lat2)**2 + (lon1 - lon2)**2)

def populate_dependencies():
    print("Loading graphs...")
    with open('src/graphs/power.json', 'r') as f:
        power = json.load(f)
    with open('src/graphs/water.json', 'r') as f:
        water = json.load(f)
    with open('src/graphs/telecom.json', 'r') as f:
        telecom = json.load(f)

    transformers = [n for n in power['nodes'] if n.get('power') == 'transformer' or n.get('power') == 'substation']
    print(f"Found {len(transformers)} power source nodes.")

    # UTM zone 43N (EPSG:32643) -> WGS84
    transformer_proj = pyproj.Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    
    def water_to_wgs84(x, y):
        lon, lat = transformer_proj.transform(x, y)
        return lat, lon

    # Update Water
    print("Updating Water dependencies...")
    for wn in water['nodes']:
        if wn.get('x') and wn.get('y'):
            lat, lon = water_to_wgs84(wn['x'], wn['y'])
            # Find nearest transformer
            nearest = min(transformers, key=lambda t: distance(lat, lon, t['latitude'], t['longitude']))
            wn['power_dependency'] = nearest['name']
            # Also save lat/lon to JSON for easier frontend loading
            wn['latitude'] = lat
            wn['longitude'] = lon

    # Update Telecom
    print("Updating Telecom dependencies...")
    for tn in telecom['nodes']:
        lat, lon = tn['latitude'], tn['longitude']
        nearest = min(transformers, key=lambda t: distance(lat, lon, t['latitude'], t['longitude']))
        tn['power_dependency'] = nearest['name']

    print("Saving updated graphs...")
    # Also save to frontend/public/data/ so the dashboard gets the latest data
    paths = [
        'src/graphs/water.json', 
        'frontend/public/data/water.json',
        'src/graphs/telecom.json',
        'frontend/public/data/telecom.json'
    ]
    for p in paths:
        with open(p, 'w') as f:
            json.dump(water if 'water' in p else telecom, f, indent=2)

    print("Done!")

if __name__ == "__main__":
    populate_dependencies()

import geopandas as gpd
from pathlib import Path

graphs_dir = Path("src/graphs")
for net in ["power", "water", "telecom"]:
    p = graphs_dir / f"{net}_nodes.gpkg"
    if p.exists():
        gdf = gpd.read_file(p).to_crs("EPSG:4326")
        bounds = gdf.total_bounds
        print(f"{net}: {bounds}")

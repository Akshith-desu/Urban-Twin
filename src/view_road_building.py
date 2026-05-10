"""
Visualise Step 1 outputs: roads, nodes, buildings, traffic signals, height restrictors
Enhanced version with better categorization and interactive features.
Opens an interactive zoomable/pannable HTML map in your browser.
"""

import geopandas as gpd
import folium
from folium.plugins import GroupedLayerControl, Fullscreen, MeasureControl
import webbrowser
import os
import numpy as np

# ── load layers ───────────────────────────────────────────────────────────────
print("Loading data files...")
nodes     = gpd.read_file("graphs/road_nodes.gpkg")
edges     = gpd.read_file("graphs/road_edges.gpkg")
buildings = gpd.read_file("data/buildings.gpkg")

# reproject all to WGS84 for folium
nodes     = nodes.to_crs("EPSG:4326")
edges     = edges.to_crs("EPSG:4326")
buildings = buildings.to_crs("EPSG:4326")

print(f"Loaded: {len(nodes)} nodes, {len(edges)} edges, {len(buildings)} buildings")

# ── colour maps (enhanced) ────────────────────────────────────────────────────
FACILITY_COLOURS = {
    # Emergency & Health (reds/oranges)
    "hospital":          "#dc2626",      # bright red
    "ambulance_station": "#ea580c",      # deep orange
    "fire_station":      "#f97316",      # orange
    "police":            "#3b82f6",      # blue
    
    # Government & Civic (purples)
    "government":        "#7c3aed",      # purple
    "courthouse":        "#7c3aed",      # purple
    "prison":            "#7c3aed",      # purple
    "community_centre":  "#8b5cf6",      # light purple
    
    # Supply & Energy (yellows/oranges)
    "fuel":              "#f59e0b",      # amber
    "marketplace":       "#fbbf24",      # yellow
    
    # Social Infrastructure (greens/teals)
    "shelter":           "#10b981",      # emerald
    "school":            "#06b6d4",      # cyan
    "college":           "#06b6d4",      # cyan
    "university":        "#06b6d4",      # cyan
    "kindergarten":      "#06b6d4",      # cyan
    
    # Industrial & Storage (grays/browns)
    "industrial":        "#6b7280",      # gray
    "warehouse":         "#78716c",      # warm gray
    
    # Commercial (amber)
    "commercial":        "#fcd34d",      # light amber
    "office":            "#fcd34d",      # light amber
    "supermarket":       "#fcd34d",      # light amber
    
    # Residential (green)
    "residential":       "#22c55e",      # green
    "apartments":        "#22c55e",      # green
    "hotel":             "#22c55e",      # green
    
    # Default
    "general":           "#9ca3af",      # cool gray
}

HIGHWAY_COLOURS = {
    "motorway":      "#ef4444",      # red
    "trunk":         "#f97316",      # orange
    "primary":       "#fbbf24",      # amber
    "secondary":     "#eab308",      # yellow
    "tertiary":      "#84cc16",      # lime
    "residential":   "#22c55e",      # green
    "unclassified":  "#a1a1aa",      # zinc
    "service":       "#d4d4d8",      # zinc light
    "living_street": "#6ee7b7",      # mint
    "pedestrian":    "#c084fc",      # purple
}

# Define criticality thresholds for node visualization
def get_node_color(criticality):
    if criticality >= 8.0:
        return "#dc2626"  # red - extremely critical
    elif criticality >= 5.0:
        return "#f97316"  # orange - highly critical
    elif criticality >= 3.0:
        return "#fbbf24"  # yellow - moderately critical
    elif criticality > 1.0:
        return "#22c55e"  # green - mildly critical
    else:
        return "#9ca3af"  # gray - standard

def get_node_size(criticality):
    return 3 + (criticality / 3)  # size 3-7 based on criticality

# ── base map (more tiles options available via layer control) ─────────────────
centre = [12.9762, 77.6265]
m = folium.Map(
    location=centre,
    zoom_start=14,
    tiles="CartoDB dark_matter",
    control_scale=True  # show scale bar
)

# Add tile layer options
folium.TileLayer('openstreetmap', name='Street Map').add_to(m)
folium.TileLayer('CartoDB positron', name='Light Background').add_to(m)
folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', 
                 attr='Esri', name='Satellite', overlay=False).add_to(m)

# Add plugins
Fullscreen().add_to(m)
MeasureControl(position='topleft', primary_length_unit='meters').add_to(m)

# ── layer groups (organized by category) ──────────────────────────────────────
lg_base_roads   = folium.FeatureGroup(name="🛣️ Roads (all)", show=True)
lg_roads_by_class = {}
for hw in HIGHWAY_COLOURS.keys():
    lg_roads_by_class[hw] = folium.FeatureGroup(name=f"🚗 {hw.title()}", show=False)

lg_base_nodes   = folium.FeatureGroup(name="📍 Road Nodes (all)", show=False)
lg_critical_nodes = folium.FeatureGroup(name="⚠️ Critical Nodes (criticality>3)", show=True)
lg_signals     = folium.FeatureGroup(name="🚦 Traffic Signals", show=True)
lg_height      = folium.FeatureGroup(name="📏 Height Restrictors", show=True)
lg_buildings   = {ft: folium.FeatureGroup(name=f"🏢 {ft.replace('_',' ').title()}", show=True)
                  for ft in FACILITY_COLOURS}
lg_other_buildings = folium.FeatureGroup(name="🏢 Other Buildings", show=True)

# ── edges (roads) by class ─────────────────────────────────────────────────────
print("Processing roads...")
for idx, row in edges.iterrows():
    if row.geometry is None or row.geometry.is_empty:
        continue
    
    hw = row.get("highway_class", "unclassified")
    if isinstance(hw, list):
        hw = hw[0] if hw else "unclassified"
    
    # Simplify geometry for large files (optional)
    if row.geometry.length > 0.01:  # in degrees, roughly 1km
        geom_simplified = row.geometry.simplify(0.0001, preserve_topology=True)
    else:
        geom_simplified = row.geometry
    
    colour = HIGHWAY_COLOURS.get(hw, "#a1a1aa")
    coords = [(y, x) for x, y in geom_simplified.coords]
    
    # Create tooltip text
    bridge_status = "Yes" if row.get('is_bridge') and row.get('is_bridge') not in [False, 0, 'no'] else "No"
    tunnel_status = "Yes" if row.get('is_tunnel') and row.get('is_tunnel') not in [False, 0, 'no'] else "No"
    health = row.get('health', 1.0)
    max_height = row.get('maxheight', row.get('height_restriction', 'unknown'))
    
    tooltip_text = f"""
    <b>Highway:</b> {hw}<br>
    <b>Length:</b> {row.geometry.length * 111000:.0f} m<br>
    <b>Bridge:</b> {bridge_status}<br>
    <b>Tunnel:</b> {tunnel_status}<br>
    <b>Surface:</b> {row.get('surface', 'unknown')}<br>
    <b>Max Height:</b> {max_height}<br>
    <b>Health:</b> {health:.2f}
    """
    
    folium.PolyLine(
        locations=coords,
        color=colour,
        weight=2.5,
        opacity=0.7,
        tooltip=folium.Tooltip(tooltip_text)
    ).add_to(lg_base_roads)
    
    # Also add to class-specific layer if it exists
    if hw in lg_roads_by_class:
        folium.PolyLine(
            locations=coords,
            color=colour,
            weight=2.5,
            opacity=0.7,
            tooltip=folium.Tooltip(tooltip_text)
        ).add_to(lg_roads_by_class[hw])

# ── nodes ─────────────────────────────────────────────────────────────────────
print("Processing nodes...")
for idx, row in nodes.iterrows():
    if row.geometry is None or row.geometry.is_empty:
        continue
    x, y = row.geometry.x, row.geometry.y
    
    # traffic signals
    highway_val = str(row.get("highway", "")).lower()
    if highway_val == "traffic_signals":
        folium.CircleMarker(
            location=[y, x],
            radius=6,
            color="#ffeb3b",
            fill=True,
            fill_color="#ffeb3b",
            fill_opacity=0.9,
            stroke=True,
            weight=1,
            tooltip="🚦 Traffic Signal"
        ).add_to(lg_signals)
        continue
    
    # height restrictors / barriers
    barrier_val = str(row.get("barrier", "")).lower()
    if barrier_val in ["height_restrictor", "lift_gate", "gate"]:
        maxheight = row.get('maxheight', 'unknown')
        folium.CircleMarker(
            location=[y, x],
            radius=6,
            color="#ff5722",
            fill=True,
            fill_color="#ff5722",
            fill_opacity=0.8,
            stroke=True,
            weight=1,
            tooltip=f"📏 {barrier_val.replace('_', ' ').title()}: {maxheight}m"
        ).add_to(lg_height)
        continue
    
    # regular nodes
    crit = float(row.get("criticality", 1.0))
    node_color = get_node_color(crit)
    node_size = get_node_size(crit)
    
    tooltip_text = f"""
    <b>Node ID:</b> {row.get('osmid', 'N/A')}<br>
    <b>Criticality:</b> {crit:.2f}<br>
    <b>Health:</b> {row.get('health', 1.0):.2f}<br>
    <b>Flood Risk:</b> {row.get('flood_risk', False)}<br>
    <b>Population Density:</b> {row.get('pop_density', 1.0):.2f}
    """
    
    # Add to all nodes layer
    folium.CircleMarker(
        location=[y, x],
        radius=2,
        color="#ffffff",
        fill=True,
        fill_color="#ffffff",
        fill_opacity=0.3,
        tooltip=folium.Tooltip(tooltip_text)
    ).add_to(lg_base_nodes)
    
    # Add to critical nodes layer if criticality > 3
    if crit > 3.0:
        folium.CircleMarker(
            location=[y, x],
            radius=node_size,
            color=node_color,
            fill=True,
            fill_color=node_color,
            fill_opacity=0.7,
            stroke=True,
            weight=1,
            tooltip=folium.Tooltip(tooltip_text)
        ).add_to(lg_critical_nodes)

# ── buildings ─────────────────────────────────────────────────────────────────
print("Processing buildings...")
building_counts = {}
for idx, row in buildings.iterrows():
    if row.geometry is None or row.geometry.is_empty:
        continue
    
    ft = row.get("facility_type", "general")
    colour = FACILITY_COLOURS.get(ft, "#9ca3af")
    crit = row.get("criticality_weight", 1.0)
    name = row.get("name", "unnamed")
    
    # Count for statistics
    building_counts[ft] = building_counts.get(ft, 0) + 1
    
    # Prepare tooltip
    amenity = row.get('amenity', '')
    building = row.get('building', '')
    tooltip_text = f"""
    <b>{name}</b><br>
    <b>Type:</b> {ft}<br>
    <b>Criticality:</b> {crit:.2f}<br>
    <b>Amenity:</b> {amenity if amenity else '-'}<br>
    <b>Building:</b> {building if building else '-'}
    """
    
    # Get style function based on type
    def style_function(feature, col=colour):
        return {
            "fillColor": col,
            "color": col if col != "#9ca3af" else "#6b7280",
            "weight": 1.5,
            "fillOpacity": 0.65,
            "opacity": 0.8
        }
    
    # Add to appropriate building layer
    if ft in lg_buildings:
        folium.GeoJson(
            data=row.geometry.__geo_interface__,
            style_function=lambda x, c=colour: {
                "fillColor": c,
                "color": c,
                "weight": 1.5,
                "fillOpacity": 0.65,
            },
            tooltip=folium.Tooltip(tooltip_text)
        ).add_to(lg_buildings[ft])
    else:
        folium.GeoJson(
            data=row.geometry.__geo_interface__,
            style_function=lambda x, c=colour: {
                "fillColor": c,
                "color": c,
                "weight": 1.5,
                "fillOpacity": 0.55,
            },
            tooltip=folium.Tooltip(tooltip_text)
        ).add_to(lg_other_buildings)

# Print building statistics
print("\nBuilding type distribution:")
for ft, count in sorted(building_counts.items(), key=lambda x: x[1], reverse=True):
    print(f"  {ft}: {count}")

# ── add all layers to map ─────────────────────────────────────────────────────
print("\nAdding layers to map...")
lg_base_roads.add_to(m)

# Add road class layers
for layer in lg_roads_by_class.values():
    layer.add_to(m)

lg_base_nodes.add_to(m)
lg_critical_nodes.add_to(m)
lg_signals.add_to(m)
lg_height.add_to(m)

# Add building layers
for layer in lg_buildings.values():
    layer.add_to(m)
lg_other_buildings.add_to(m)

# Layer control with grouping
folium.LayerControl(collapsed=False).add_to(m)

# ── enhanced legend ───────────────────────────────────────────────────────────
legend_html = """
<div style="
    position: fixed; 
    bottom: 20px; 
    left: 20px; 
    z-index: 1000;
    background: rgba(0,0,0,0.85); 
    backdrop-filter: blur(8px);
    padding: 12px 18px;
    border-radius: 12px; 
    color: #e5e5e5; 
    font-family: 'Segoe UI', Arial, monospace;
    font-size: 11px; 
    line-height: 1.6; 
    max-height: 70vh; 
    overflow-y: auto;
    border: 1px solid rgba(255,255,255,0.2);
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
">
<b style="font-size: 13px;">📋 LEGEND</b><br><br>

<b style="color: #fbbf24;">🏥 FACILITY TYPES</b><br>
"""
for ft, colour in FACILITY_COLOURS.items():
    if ft in building_counts or ft == "general":
        display_name = ft.replace('_', ' ').title()
        count = building_counts.get(ft, 0)
        legend_html += (
            f'<span style="display:inline-block;width:14px;height:14px;'
            f'background:{colour};border-radius:3px;margin-right:8px;"></span>'
            f'{display_name} <span style="color:#9ca3af;">({count})</span><br>'
        )

legend_html += "<br><b style='color: #fbbf24;'>🛣️ ROAD CLASSES</b><br>"
for hw, colour in HIGHWAY_COLOURS.items():
    legend_html += (
        f'<span style="display:inline-block;width:24px;height:3px;'
        f'background:{colour};margin-right:8px;vertical-align:middle;"></span>'
        f'{hw.title()}<br>'
    )

legend_html += """
<br><b style='color: #fbbf24;'>⚠️ NODE CRITICALITY</b><br>
<span style="display:inline-block;width:14px;height:14px;background:#dc2626;border-radius:7px;margin-right:8px;"></span> >8.0 (Extreme)<br>
<span style="display:inline-block;width:14px;height:14px;background:#f97316;border-radius:7px;margin-right:8px;"></span> 5.0-8.0 (High)<br>
<span style="display:inline-block;width:14px;height:14px;background:#fbbf24;border-radius:7px;margin-right:8px;"></span> 3.0-5.0 (Moderate)<br>
<span style="display:inline-block;width:14px;height:14px;background:#22c55e;border-radius:7px;margin-right:8px;"></span> 1.0-3.0 (Mild)<br>
<span style="display:inline-block;width:14px;height:14px;background:#9ca3af;border-radius:7px;margin-right:8px;"></span> =1.0 (Standard)<br>

<br><b style='color: #fbbf24;'>📍 OTHER FEATURES</b><br>
<span style="display:inline-block;width:14px;height:14px;background:#ffeb3b;border-radius:7px;margin-right:8px;"></span> Traffic Signal<br>
<span style="display:inline-block;width:14px;height:14px;background:#ff5722;border-radius:7px;margin-right:8px;"></span> Height Restrictor<br>

<hr style="margin: 8px 0; border-color: #374151;">
<span style="font-size: 10px; color: #9ca3af;">▼ Use layer control (top-right) to toggle categories</span>
</div>
"""

m.get_root().html.add_child(folium.Element(legend_html))

# Add coordinate display on click
m.add_child(folium.LatLngPopup())

# ── save + open ───────────────────────────────────────────────────────────────
out_path = os.path.abspath("road_network_map.html")
m.save(out_path)
print(f"\n✅ Saved: {out_path}")
print(f"📊 File size: {os.path.getsize(out_path) / 1024 / 1024:.2f} MB")
webbrowser.open(f"file://{out_path}")

print("\n🎉 Visualization complete! The map includes:")
print("   • Roads color-coded by class with tooltips")
print("   • Buildings color-coded by facility type")
print("   • Critical nodes highlighted by criticality level")
print("   • Traffic signals and height restrictors")
print("   • Layer controls to toggle visibility")
print("   • Fullscreen and measurement tools")
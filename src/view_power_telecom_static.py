"""
Simple static visualization of Power & Telecom infrastructure
Creates a static PNG image showing all nodes and edges
"""

import geopandas as gpd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os

# ── load layers ───────────────────────────────────────────────────────────────
print("Loading power and telecom data...")

# Power layers
power_nodes = gpd.read_file("graphs/power_nodes.gpkg")
power_edges = gpd.read_file("graphs/power_edges.gpkg")

# Telecom layers
telecom_nodes = gpd.read_file("graphs/telecom_nodes.gpkg")
telecom_edges = gpd.read_file("graphs/telecom_edges.gpkg")

print(f"Power:   {len(power_nodes)} nodes, {len(power_edges)} edges")
print(f"Telecom: {len(telecom_nodes)} nodes, {len(telecom_edges)} edges")

# ── Define colors for different node types ────────────────────────────────────
POWER_COLORS = {
    "substation":   "red",
    "transformer":  "orange", 
    "generator":    "yellow",
    "plant":        "darkred",
    "data_center":  "purple",
    "fuel":         "brown",
    "default":      "gray"
}

TELECOM_COLORS = {
    "exchange":     "darkred",
    "data_center":  "purple",
    "mast":         "blue",
    "tower":        "cyan",
    "cabinet":      "green",
    "terminal":     "lightgreen",
    "default":      "lightblue"
}

# ── Create figure with subplots ───────────────────────────────────────────────
fig, axes = plt.subplots(2, 2, figsize=(16, 14))
fig.suptitle('Power & Telecom Infrastructure - Halasuru, Bengaluru', fontsize=16, fontweight='bold')

# 1. Power Nodes Only
ax1 = axes[0, 0]
ax1.set_title('Power Nodes by Type', fontsize=12, fontweight='bold')

if len(power_nodes) > 0:
    # Plot each power node type with different color
    for node_type, color in POWER_COLORS.items():
        subset = power_nodes[power_nodes['node_type'] == node_type]
        if len(subset) > 0:
            subset.plot(ax=ax1, color=color, marker='o', markersize=50, 
                       label=f"{node_type} ({len(subset)})", alpha=0.7, edgecolor='black')
    
    ax1.legend(loc='upper right', fontsize=8)
    ax1.set_xlabel('Easting (m)')
    ax1.set_ylabel('Northing (m)')
    ax1.grid(True, alpha=0.3)
else:
    ax1.text(0.5, 0.5, 'No power nodes found', transform=ax1.transAxes, 
             ha='center', va='center', fontsize=14)

# 2. Telecom Nodes Only
ax2 = axes[0, 1]
ax2.set_title('Telecom Nodes by Type', fontsize=12, fontweight='bold')

if len(telecom_nodes) > 0:
    for node_type, color in TELECOM_COLORS.items():
        subset = telecom_nodes[telecom_nodes['node_type'] == node_type]
        if len(subset) > 0:
            ax2.scatter(subset.geometry.x, subset.geometry.y, 
                       c=color, s=100, label=f"{node_type} ({len(subset)})", 
                       alpha=0.7, edgecolor='black', linewidth=1)
    
    ax2.legend(loc='upper right', fontsize=8)
    ax2.set_xlabel('Easting (m)')
    ax2.set_ylabel('Northing (m)')
    ax2.grid(True, alpha=0.3)
else:
    ax2.text(0.5, 0.5, 'No telecom nodes found', transform=ax2.transAxes, 
             ha='center', va='center', fontsize=14)

# 3. Power Network (Nodes + Edges)
ax3 = axes[1, 0]
ax3.set_title('Power Network', fontsize=12, fontweight='bold')

if len(power_edges) > 0:
    # Plot edges first (so they appear behind nodes)
    power_edges.plot(ax=ax3, color='gray', linewidth=1, alpha=0.5, label='Power Lines')
    
    # Then plot nodes
    for node_type, color in POWER_COLORS.items():
        subset = power_nodes[power_nodes['node_type'] == node_type]
        if len(subset) > 0:
            subset.plot(ax=ax3, color=color, marker='o', markersize=80,
                       label=f"{node_type} ({len(subset)})", alpha=0.8, edgecolor='black')
    
    ax3.legend(loc='upper right', fontsize=8, ncol=2)
    ax3.set_xlabel('Easting (m)')
    ax3.set_ylabel('Northing (m)')
    ax3.grid(True, alpha=0.3)
else:
    ax3.text(0.5, 0.5, 'No power edges found', transform=ax3.transAxes, 
             ha='center', va='center', fontsize=14)

# 4. Telecom Network (Nodes + Edges)
ax4 = axes[1, 1]
ax4.set_title('Telecom Network', fontsize=12, fontweight='bold')

if len(telecom_edges) > 0:
    # Plot edges first
    telecom_edges.plot(ax=ax4, color='lightblue', linewidth=2, alpha=0.5, label='Fiber Links')
    
    # Plot nodes
    for node_type, color in TELECOM_COLORS.items():
        subset = telecom_nodes[telecom_nodes['node_type'] == node_type]
        if len(subset) > 0:
            ax4.scatter(subset.geometry.x, subset.geometry.y, 
                       c=color, s=120, label=f"{node_type} ({len(subset)})", 
                       alpha=0.8, edgecolor='black', linewidth=1.5)
    
    ax4.legend(loc='upper right', fontsize=8)
    ax4.set_xlabel('Easting (m)')
    ax4.set_ylabel('Northing (m)')
    ax4.grid(True, alpha=0.3)
else:
    ax4.text(0.5, 0.5, 'No telecom edges found', transform=ax4.transAxes, 
             ha='center', va='center', fontsize=14)

# Add summary statistics as text
summary_text = f"""
Infrastructure Summary:
• Power Nodes: {len(power_nodes)}
  - Substations: {len(power_nodes[power_nodes['node_type'] == 'substation'])}
  - Transformers: {len(power_nodes[power_nodes['node_type'] == 'transformer'])}
  - Fuel stations: {len(power_nodes[power_nodes['node_type'] == 'fuel'])}
  - Generators: {len(power_nodes[power_nodes['node_type'] == 'generator'])}
• Power Edges: {len(power_edges)}
• Telecom Nodes: {len(telecom_nodes)} (all masts)
• Telecom Edges: {len(telecom_edges)}
"""

fig.text(0.02, 0.02, summary_text, fontsize=10, family='monospace',
         bbox=dict(boxstyle="round,pad=0.5", facecolor='lightgray', alpha=0.8))

plt.tight_layout()
plt.subplots_adjust(bottom=0.15)

# Save the figure
output_file = "infrastructure_static_map.png"
plt.savefig(output_file, dpi=150, bbox_inches='tight')
print(f"\n✅ Saved static map to: {output_file}")

# Also create a combined view with all infrastructure
fig2, ax = plt.subplots(1, 1, figsize=(14, 12))
ax.set_title('All Infrastructure: Power + Telecom', fontsize=14, fontweight='bold')

# Plot power edges
if len(power_edges) > 0:
    power_edges.plot(ax=ax, color='orange', linewidth=1, alpha=0.4, label='Power Lines')

# Plot telecom edges
if len(telecom_edges) > 0:
    telecom_edges.plot(ax=ax, color='cyan', linewidth=2, alpha=0.4, label='Fiber Links')

# Plot power nodes
for node_type, color in POWER_COLORS.items():
    subset = power_nodes[power_nodes['node_type'] == node_type]
    if len(subset) > 0:
        subset.plot(ax=ax, color=color, marker='s', markersize=60,
                   label=f"Power: {node_type}", alpha=0.8, edgecolor='black')

# Plot telecom nodes
for node_type, color in TELECOM_COLORS.items():
    subset = telecom_nodes[telecom_nodes['node_type'] == node_type]
    if len(subset) > 0:
        ax.scatter(subset.geometry.x, subset.geometry.y, 
                   c=color, s=100, marker='o',
                   label=f"Telecom: {node_type}", alpha=0.8, edgecolor='black', linewidth=1)

ax.legend(loc='upper right', fontsize=9, ncol=2)
ax.set_xlabel('Easting (m)')
ax.set_ylabel('Northing (m)')
ax.grid(True, alpha=0.3, linestyle='--')
ax.set_title('Complete Infrastructure Network - Halasuru, Bengaluru', fontsize=14, fontweight='bold')

combined_file = "infrastructure_combined.png"
plt.savefig(combined_file, dpi=150, bbox_inches='tight')
print(f"✅ Saved combined map to: {combined_file}")

# Print node coordinates for debugging
print("\n=== POWER NODE COORDINATES (first 5) ===")
for idx, row in power_nodes.head().iterrows():
    print(f"  {row['node_type']:15s}: ({row.geometry.x:.1f}, {row.geometry.y:.1f})")

print("\n=== TELECOM NODE COORDINATES ===")
for idx, row in telecom_nodes.iterrows():
    print(f"  {row['node_type']:15s}: ({row.geometry.x:.1f}, {row.geometry.y:.1f})")

# Try to open the images
try:
    from PIL import Image
    img = Image.open(output_file)
    print(f"\n✅ Images created successfully!")
    print(f"   - {output_file}")
    print(f"   - {combined_file}")
    print("\nOpen these files in any image viewer to see your infrastructure.")
except Exception as e:
    print(f"\n✅ Images saved but couldn't preview: {e}")
    print(f"   Please open {output_file} manually.")
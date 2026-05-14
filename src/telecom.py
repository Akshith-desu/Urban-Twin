"""
telecom.py
REVISED ARCHITECTURE:
- NO backhaul network
- NO telecom edges
- ONE physical tower node
- Multiple providers/services nested inside each tower
- Exports:
    1. telecom_nodes.gpkg
    2. telecom.json
    3. telecom.png

Visualization:
- 4 subplots:
    Jio / Airtel / Vi / BSNL
- Coverage circles shown per technology
- Different colours for:
    2G / 3G / 4G / 5G

===========================================================================
"""

import os
import json
import random
import warnings
import numpy as np
import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
from shapely.geometry import Point
warnings.filterwarnings("ignore")

# ============================================================================
# CONFIGURATION
# ============================================================================

CRS = "EPSG:32643"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "graphs")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================================
# TOWER INPUTS
# ============================================================================

# (latitude, longitude, tower_type, providers...)

TOWER_LOCATIONS = [

    (12.9733, 77.6225, "ground", "Jio_4G"),
    (12.9750, 77.6082, "ground", "Airtel_2G_4G"),
    (12.9757, 77.6083, "ground", "Airtel_2G_4G"),
    (12.9671, 77.6267, "ground",
     "Vi_2G_4G",
     "Airtel_4G_5G",
     "Jio_4G_5G"),
    (12.9644, 77.6275, "ground",
     "Vi_2G_4G",
     "Airtel_3G_4G_5G"),
    (12.9643, 77.6320, "ground",
     "Vi_2G_4G",
     "Airtel_3G_4G_5G"),
    (12.9617, 77.6311, "ground",
     "Airtel_4G"),
    (12.9587, 77.6264, "ground",
     "Airtel_4G"),
    (12.9823, 77.6347, "ground",
     "Jio_4G_5G"),
    (12.9859, 77.6288, "ground",
     "Vi_2G_4G",
     "Airtel_2G_4G_5G",
     "Jio_4G"),
    (12.9847, 77.6124, "ground",
     "BSNL_2G_3G_4G"),
    (12.9615, 77.6130, "ground",
     "Airtel_4G_5G"),
    (12.9682, 77.6127, "ground",
     "Airtel_4G"),
    (12.9736, 77.6219, "wall",
     "Jio_4G_5G"),
    (12.9733, 77.6093, "wall",
     "Jio_4G_5G"),
]

# ============================================================================
# FREQUENCY BANDS
# ============================================================================

OPERATOR_TECH_BANDS = {

    "Jio": {
        "2G": None,
        "3G": None,
        "4G": [850, 1800, 2300],
        "5G": [3500]
    },
    "Airtel": {
        "2G": [900],
        "3G": [2100],
        "4G": [1800, 2100, 2300],
        "5G": [3500]
    },
    "Vi": {
        "2G": [900],
        "3G": [2100],
        "4G": [1800, 2500],
        "5G": [3500]
    },
    "BSNL": {
        "2G": [850],
        "3G": [2100],
        "4G": [850, 2300],
        "5G": None
    }
}

# ============================================================================
# TOWER SPECS
# ============================================================================

TOWER_SPECS = {

    "ground": {
        "transmit_power_dbm": (40, 46),
        "antenna_gain_dbi": 15,
        "power_consumption_kw": (3, 6),
        "battery_hours": (4, 8)
    },

    "wall": {
        "transmit_power_dbm": (27, 33),
        "antenna_gain_dbi": 5,
        "power_consumption_kw": (1, 3),
        "battery_hours": (4, 8)
    }
}

# ============================================================================
# CONSTANTS
# ============================================================================

RECEIVE_GAIN_DBI = -2.0
RSS_THRESHOLD_DBM = -110.0
ENVIRONMENTAL_LOSS_BY_FREQUENCY = {
    700:  (75.0, 95.0),
    900:  (70.0, 90.0),
    1800: (60.0, 75.0),
    2100: (60.0, 70.0),
    2300: (60.0, 70.0),
    2600: (60.0, 75.0),
}

TECH_COLOURS = {

    "2G": "#ef4444",   # red
    "3G": "#f59e0b",   # orange
    "4G": "#3b82f6",   # blue
    "5G": "#22c55e",   # green
}

PROVIDER_PLOT_ORDER = [
    "Jio",
    "Airtel",
    "Vi",
    "BSNL"
]
# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def parse_provider_string(provider_string):

    parts = provider_string.split("_")
    operator = parts[0]
    technologies = parts[1:]
    return operator, technologies

def get_frequency(operator, technology):
    bands = OPERATOR_TECH_BANDS.get(operator, {}).get(technology)
    if bands is None:
        return None
    return random.choice(bands)

def calculate_fspl(distance_m, frequency_mhz):
    """
    Free Space Path Loss (dB)
    FSPL = 20·log10(d) + 20·log10(f) - 27.55
    """
    if distance_m <= 0:
        return 0
    return 20 * np.log10(distance_m) + 20 * np.log10(frequency_mhz) - 27.55

def calculate_rss_friis(
    distance_m,
    frequency_mhz,
    transmit_power_dbm,
    transmit_gain_dbi,
    receive_gain_dbi,
    environmental_loss_db
):
    """
    Friis Transmission Equation
    RSS = Pt + Gt + Gr - FSPL - Lenv
    """
    fspl_db = calculate_fspl(distance_m, frequency_mhz)
    rss_dbm = (
        transmit_power_dbm
        + transmit_gain_dbi
        + receive_gain_dbi
        - fspl_db
        - environmental_loss_db
    )
    return rss_dbm

def calculate_coverage_radius(
    frequency_mhz,
    transmit_power_dbm,
    transmit_gain_dbi,
    receive_gain_dbi,
    environmental_loss_db,
    rss_threshold_dbm=-110
):
    """
    Closed-form solution for maximum coverage radius using Friis equation.
    """
    # Step 1: required FSPL at edge
    fspl_required = (
        transmit_power_dbm
        + transmit_gain_dbi
        + receive_gain_dbi
        - environmental_loss_db
        - rss_threshold_dbm
    )

    # Step 2: invert FSPL to distance
    exponent = (
        fspl_required
        - 20 * np.log10(frequency_mhz)
        + 27.55
    ) / 20

    distance_m = 10 ** exponent

    return max(distance_m, 0)
# ============================================================================
# CREATE TELECOM NODES
# ============================================================================

def create_telecom_nodes():

    print("\nCreating telecom nodes...")

    tower_records = []
    ground_counter = 0
    wall_counter = 0

    for idx, tower in enumerate(TOWER_LOCATIONS):

        lat = tower[0]
        lon = tower[1]
        tower_type = tower[2]
        provider_strings = tower[3:]
        # ------------------------------------------------------------
        # tower naming
        # ------------------------------------------------------------
        if tower_type == "ground":
            ground_counter += 1
            tower_name = f"Ground Tower {ground_counter}"

        else:
            wall_counter += 1
            tower_name = f"Wall Tower {wall_counter}"
        # ------------------------------------------------------------
        # tower-level shared parameters
        # ------------------------------------------------------------

        power_kw = round(random.uniform(
            *TOWER_SPECS[tower_type]["power_consumption_kw"]), 2)

        battery_hours = round(random.uniform(
            *TOWER_SPECS[tower_type]["battery_hours"]), 1)

        battery_capacity = round(power_kw * battery_hours, 2)

        # geometry
        point_wgs84 = gpd.GeoSeries(
            [Point(lon, lat)],
            crs="EPSG:4326"
        )

        point_utm = point_wgs84.to_crs(CRS)[0]

        # ------------------------------------------------------------
        # provider list
        # ------------------------------------------------------------
        providers = []

        for provider_string in provider_strings:

            operator, technologies = parse_provider_string(provider_string)
            for technology in technologies:
                frequency = get_frequency(operator, technology)
                if frequency is None:
                    continue
                tx_power = round(random.uniform(
                    *TOWER_SPECS[tower_type]["transmit_power_dbm"]), 2)
                loss_range = ENVIRONMENTAL_LOSS_BY_FREQUENCY.get(
                     frequency,
                    (60.0, 75.0)    # fallback if frequency not in dict
                )
                environmental_loss = round(random.uniform(*loss_range), 2)
                antenna_gain = TOWER_SPECS[tower_type]["antenna_gain_dbi"]
                coverage_radius = calculate_coverage_radius(
                    frequency_mhz=frequency,
                    transmit_power_dbm=tx_power,
                    transmit_gain_dbi=antenna_gain,
                    receive_gain_dbi=RECEIVE_GAIN_DBI,
                    environmental_loss_db=environmental_loss
                )

                # example RSS at coverage edge
                rss = (
                    tx_power
                    + antenna_gain
                    + RECEIVE_GAIN_DBI
                    - calculate_fspl(coverage_radius, frequency)
                    - environmental_loss
                )

                provider_entry = {

                    "operator": operator,
                    "technology": technology,
                    "frequency_mhz": frequency,
                    "transmit_power_dbm": tx_power,
                    "antenna_gain_dbi": antenna_gain,
                    "receive_gain_dbi": RECEIVE_GAIN_DBI,
                    "environmental_loss_db": environmental_loss,
                    "coverage_radius_m": round(coverage_radius, 2),
                    "rss_dbm": round(rss, 2)
                }

                providers.append(provider_entry)

        # ------------------------------------------------------------
        # final tower record
        # ------------------------------------------------------------

        tower_record = {

            "node_id": idx + 1,
            "name": tower_name,
            "tower_type": tower_type,
            "latitude": lat,
            "longitude": lon,
            "health": 1.0,
            "operational_status": "normal",
            "power_consumption_kw": power_kw,
            "geometry": point_utm,
            "battery": {
                "capacity_kwh": battery_capacity,
                "remaining_kwh": battery_capacity,
                "backup_hours": battery_hours,
                "on_battery": False
            },
            "providers": providers
        }

        tower_records.append(tower_record)

        print(f"\n[{idx+1}] {tower_name}")
        print(f"  Providers: {len(providers)}")

    return tower_records

# ============================================================================
# EXPORT GPKG
# ============================================================================

def export_gpkg(tower_records):

    print("\nExporting telecom_nodes.gpkg...")
    rows = []
    for tower in tower_records:
        rows.append({
            "node_id": tower["node_id"],
            "name": tower["name"],
            "tower_type": tower["tower_type"],
            "latitude": tower["latitude"],
            "longitude": tower["longitude"],
            "health": tower["health"],
            "operational_status": tower["operational_status"],
            "power_consumption_kw": tower["power_consumption_kw"],
            "battery_capacity_kwh":
                tower["battery"]["capacity_kwh"],
            "battery_remaining_kwh":
                tower["battery"]["remaining_kwh"],
            "battery_backup_hours":
                tower["battery"]["backup_hours"],
            "on_battery":
                tower["battery"]["on_battery"],
            "providers_json":
                json.dumps(tower["providers"]),
            "geometry":
                tower["geometry"]
        })

    gdf = gpd.GeoDataFrame(rows, crs=CRS)

    output_path = os.path.join(OUTPUT_DIR, "telecom_nodes.gpkg")
    gdf.to_file(output_path, driver="GPKG")
    print(f"Saved: {output_path}")

    return gdf

# ============================================================================
# EXPORT JSON
# ============================================================================

def export_json(tower_records):
    print("\nExporting telecom.json...")
    output_path = os.path.join(OUTPUT_DIR, "telecom.json")
    json_data = {

        "metadata": {
            "total_towers": len(tower_records),
            "rss_threshold_dbm": RSS_THRESHOLD_DBM
        },
        "nodes": []
    }

    for tower in tower_records:

        json_data["nodes"].append({
            "node_id": tower["node_id"],
            "name": tower["name"],
            "tower_type": tower["tower_type"],
            "latitude": tower["latitude"],
            "longitude": tower["longitude"],
            "health": tower["health"],
            "operational_status":
                tower["operational_status"],
            "power_consumption_kw":
                tower["power_consumption_kw"],
            "battery":
                tower["battery"],
            "providers":
                tower["providers"]
        })

    with open(output_path, "w") as f:
        json.dump(json_data, f, indent=2)

    print(f"Saved: {output_path}")


# ============================================================================
# VISUALIZATION
# ============================================================================

def plot_network(tower_records):

    print("\nGenerating telecom.png...")
    fig, axes = plt.subplots(2, 2, figsize=(18, 18))
    fig.patch.set_facecolor("#111827")
    provider_axes = {

        "Jio": axes[0, 0],
        "Airtel": axes[0, 1],
        "Vi": axes[1, 0],
        "BSNL": axes[1, 1],
    }
    for provider_name, ax in provider_axes.items():
        ax.set_facecolor("#1f2937")
        # ------------------------------------------------------------
        # plot coverage circles
        # ------------------------------------------------------------
        for tower in tower_records:

            lon = tower["longitude"]
            lat = tower["latitude"]
            for provider in tower["providers"]:

                if provider["operator"] != provider_name:
                    continue
                technology = provider["technology"]
                radius_deg = (
                    provider["coverage_radius_m"] / 111320
                )
                colour = TECH_COLOURS.get(
                    technology,
                    "#ffffff"
                )
                circle = plt.Circle(
                    (lon, lat),
                    radius_deg,
                    color=colour,
                    alpha=0.15,
                    ec=colour,
                    lw=2
                )
                ax.add_patch(circle)
                ax.scatter(
                    lon,
                    lat,
                    color=colour,
                    edgecolors="white",
                    s=60,
                    zorder=5
                )
        # ------------------------------------------------------------
        # legend
        # ------------------------------------------------------------
        handles = []
        for tech, colour in TECH_COLOURS.items():
            handles.append(
                plt.Line2D(
                    [0],
                    [0],
                    marker='o',
                    color='w',
                    markerfacecolor=colour,
                    markersize=10,
                    label=tech
                )
            )

        ax.legend(
            handles=handles,
            facecolor="#111827",
            edgecolor="#444",
            labelcolor="white",
            loc="upper right"
        )

        ax.set_title(
            provider_name,
            color="white",
            fontsize=16
        )

        ax.tick_params(colors="white")

        for spine in ax.spines.values():
            spine.set_edgecolor("#444")

        ax.set_xlabel("Longitude", color="white")
        ax.set_ylabel("Latitude", color="white")

    plt.suptitle(
        "Telecom Coverage by Provider and Technology",
        color="white",
        fontsize=20
    )

    plt.tight_layout()

    output_path = os.path.join(OUTPUT_DIR, "telecom.png")

    plt.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
        facecolor="#111827"
    )

    plt.show()

    print(f"Saved: {output_path}")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":

    print("\n====================================================")
    print("TELECOM INFRASTRUCTURE MODEL")
    print("====================================================")
    towers = create_telecom_nodes()
    telecom_nodes_gdf = export_gpkg(towers)
    export_json(towers)
    plot_network(towers)

    print("\n====================================================")
    print("PROCESS COMPLETED SUCCESSFULLY")
    print("====================================================")
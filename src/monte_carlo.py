"""
monte_carlo.py — Monte Carlo Simulation Engine
Runs a user-chosen disaster scenario N times (default 1000) across
Power, Water, and Telecom networks with different random seeds.

Collects per-run metrics and produces:
  - Console summary with mean, std, 95% CI
  - CSV file at data/monte_carlo_results.csv
  - Statistics summary at data/monte_carlo_summary.txt

Usage:
    python monte_carlo.py --fail "Substation 1"    # default: fail substation 1
    python monte_carlo.py --fail 35 38 "Tower 1"   # fail multiple nodes at once
    python monte_carlo.py --runs 500 --ticks 60    # 500 runs, 60 ticks each
    python monte_carlo.py --flood                  # add flood at tick 5
"""

import asyncio
import argparse
import csv
import json
import logging
import math
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from event_schema import (
    Event, EventType, Network, TICK_DURATION_MINUTES,
    sim_tick, user_fail_node,
)
from event_bus import EventBus
from power_agent import PowerAgent
from water_agent import WaterAgent
from telecom_agent import TelecomAgent

# Import constants for full physics scrambling
import power
import water_network
import telecom

# ── colours ───────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"


# ══════════════════════════════════════════════════════════════════════════════
# SINGLE RUN
# ══════════════════════════════════════════════════════════════════════════════

async def run_single_simulation(
    run_id: int,
    seed: int,
    fail_targets: list[dict],
    flood_junction: str | None,
    flood_tower: str | None,
    n_ticks: int,
    power_json: str,
    water_json: str,
    telecom_json: str,
) -> dict:
    """
    Execute one complete simulation run and return metrics dict.
    """
    # Set the random seed for this run
    random.seed(seed)

    # Create fresh bus (no file logging for speed)
    bus = EventBus(log_path=f"data/_mc_null.jsonl", maxsize=50_000)
    await bus.start()

    # Create fresh agents (re-loads JSON = clean state)
    power_agent  = PowerAgent(bus, power_json_path=power_json)
    water_agent  = WaterAgent(bus, water_json_path=water_json)
    telecom_agent = TelecomAgent(bus, telecom_json_path=telecom_json,
                                  power_json_path=power_json)

    # Start agents
    power_task   = asyncio.create_task(power_agent.start())
    water_task   = asyncio.create_task(water_agent.start())
    telecom_task = asyncio.create_task(telecom_agent.start())
    await asyncio.sleep(0.02)

    # ── Inject Baseline Stochasticity (Re-randomize ALL Physics) ──────────────
    # The user is 100% correct. We shouldn't just scramble 3 variables.
    # We must re-sample EVERY SINGLE parameter that was randomized during 
    # graph creation so that every Monte Carlo run is a completely unique 
    # state space evaluation.
    scramble_all_physics(power_agent, water_agent, telecom_agent)

    # ── Tick 0: Fail the chosen targets ───────────────────────────────────────
    for target in fail_targets:
        await bus.publish(user_fail_node(target["network"], target["id"], tick=0))
    await asyncio.sleep(0.02)

    # ── Run ticks ─────────────────────────────────────────────────────────────
    for t in range(n_ticks):
        # Inject flood at tick 5 if requested
        if t == 5:
            if flood_junction:
                await bus.publish(Event(
                    EventType.FLOOD_NODE, Network.SYSTEM, flood_junction,
                    severity=0.8, tick=t,
                    metadata={"flood_severity": 0.8}
                ))
            if flood_tower:
                await bus.publish(Event(
                    EventType.FLOOD_NODE, Network.SYSTEM, flood_tower,
                    severity=0.7, tick=t,
                    metadata={"flood_severity": 0.7}
                ))

        await bus.publish(sim_tick(tick=t))
        await asyncio.sleep(0.005)  # minimal delay for queue processing

    # ── Collect metrics ───────────────────────────────────────────────────────
    # Power
    p_failed   = len(power_agent.get_failed_nodes())
    p_degraded = len(power_agent.get_degraded_nodes())

    # Water
    w_pumps_failed = sum(1 for p in water_agent._pump_ids
                         if water_agent.nodes[p].get("pump_status") == "failed")
    w_pumps_backup = sum(1 for p in water_agent._pump_ids
                         if water_agent.nodes[p].get("pump_status") == "on_backup")
    w_towers_draining = sum(1 for t_id in water_agent._tower_ids
                            if water_agent.nodes[t_id].get("is_draining"))
    w_towers_empty = sum(1 for t_id in water_agent._tower_ids
                         if water_agent.nodes[t_id].get("water_level", 1.0) <= 0.01)
    w_bursts = sum(1 for j in water_agent._junction_ids
                   if water_agent.nodes[j].get("burst_occurred"))

    junctions = [water_agent.nodes[j] for j in water_agent._junction_ids]
    w_avg_pressure = (sum(j.get("pressure", 0) for j in junctions) / len(junctions)
                      if junctions else 0)
    w_min_pressure = min((j.get("pressure", 1.0) for j in junctions), default=1.0)

    # Telecom
    t_on_battery = sum(1 for n in telecom_agent.nodes.values()
                       if n["battery"].get("on_battery"))
    t_failed = len(telecom_agent.get_failed_nodes())

    batt_pcts = []
    for n in telecom_agent.nodes.values():
        cap = max(n["battery"].get("capacity_kwh", 1), 0.001)
        rem = n["battery"].get("remaining_kwh", cap)
        batt_pcts.append(rem / cap * 100)
    t_avg_batt = sum(batt_pcts) / len(batt_pcts) if batt_pcts else 100.0
    t_min_batt = min(batt_pcts) if batt_pcts else 100.0

    total_events = bus.published_count

    # ── Cleanup ───────────────────────────────────────────────────────────────
    power_task.cancel()
    water_task.cancel()
    telecom_task.cancel()
    await bus.stop()

    return {
        "run_id":                run_id,
        "seed":                  seed,
        "power_failed":          p_failed,
        "power_degraded":        p_degraded,
        "water_pumps_failed":    w_pumps_failed,
        "water_pumps_backup":    w_pumps_backup,
        "water_towers_draining": w_towers_draining,
        "water_towers_empty":    w_towers_empty,
        "water_pipe_bursts":     w_bursts,
        "water_avg_pressure":    round(w_avg_pressure, 4),
        "water_min_pressure":    round(w_min_pressure, 4),
        "telecom_on_battery":    t_on_battery,
        "telecom_failed":        t_failed,
        "telecom_avg_batt_pct":  round(t_avg_batt, 2),
        "telecom_min_batt_pct":  round(t_min_batt, 2),
        "total_events":          total_events,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHYSICS SCRAMBLER
# ══════════════════════════════════════════════════════════════════════════════

def scramble_all_physics(pa: PowerAgent, wa: WaterAgent, ta: TelecomAgent):
    """
    Re-samples every stochastic variable from power.py, water_network.py, 
    and telecom.py. This "cuts the tie" to the static JSON files.
    """
    # ── 1. Scramble Power Network ─────────────────────────────────────────────
    for n in pa.nodes.values():
        ntype = n.get("power")
        if ntype == "substation":
            n["rated_capacity_mw"] = round(random.uniform(*power.SUBSTATION_RATED_CAPACITY_MW_RANGE), 2)
            n["impedance_ohm"]     = round(random.uniform(*power.SUBSTATION_IMPEDANCE_OHM_RANGE), 4)
            n["relay_TMS"]         = round(random.uniform(*power.SUBSTATION_RELAY_TMS_RANGE), 3)
            n["load_fraction"]     = round(random.uniform(0.4, 0.85), 3)
        elif ntype == "transformer":
            n["capacity_kva"]      = random.choice(power.TRANSFORMER_CAPACITIES_KVA)
            n["impedance_ohm"]     = round(random.uniform(*power.TRANSFORMER_IMPEDANCE_OHM_RANGE), 5)
            n["thermal_time_constant_min"] = round(random.uniform(*power.TRANSFORMER_THERMAL_TIME_CONST_RANGE), 1)
            n["cooling_type"]      = random.choice(power.TRANSFORMER_COOLING_TYPES)
            n["current_temperature_c"] = round(random.uniform(*power.TRANSFORMER_INIT_TEMP_C_RANGE), 1)
            n["load_fraction"]     = round(random.uniform(0.4, 0.85), 3)
            
    # power edges (feeders) - pa.nodes contains downstream nodes, but edge stats are passed in events.
    # Currently power edges aren't stored as a flat list in PowerAgent, but the nodes hold thermal limits
    # Wait, feeder physics are actually evaluated in the node logic in Phase 3.

    # ── 2. Scramble Water Network ─────────────────────────────────────────────
    for n in wa.nodes.values():
        ntype = n.get("node_type")
        if ntype == "pump_station":
            n["pipe_material"] = random.choice(water_network.PUMP_PIPE_MATERIAL_OPTIONS)
            n["pipe_age_years"] = random.uniform(*water_network.PUMP_PIPE_AGE_YEARS_RANGE)
            n["pipe_diameter_mm"] = random.randint(*water_network.PUMP_PIPE_DIAMETER_MM_RANGE)
            n["backup_gen_runtime_h"] = round(random.uniform(*water_network.PUMP_BACKUP_GEN_RUNTIME_H_RANGE), 2)
            n["backup_gen_remaining_h"] = n["backup_gen_runtime_h"]
            n["pressure"] = round(random.uniform(*water_network.PUMP_INITIAL_PRESSURE_RANGE), 3)
            n["storage_capacity_m3"] = round(random.uniform(*water_network.PUMP_STORAGE_CAPACITY_M3_RANGE), 1)
            n["base_flow"] = round(random.uniform(*water_network.PUMP_BASE_FLOW_RATE_RANGE), 3)
        elif ntype == "water_tower":
            n["pipe_material"] = random.choice(water_network.TOWER_PIPE_MATERIAL_OPTIONS)
            n["pipe_age_years"] = random.uniform(*water_network.TOWER_PIPE_AGE_YEARS_RANGE)
            n["pipe_diameter_mm"] = random.randint(*water_network.TOWER_PIPE_DIAMETER_MM_RANGE)
            n["tower_height_m"] = round(random.uniform(*water_network.TOWER_HEIGHT_M_RANGE), 1)
            n["water_level"] = round(random.uniform(*water_network.TOWER_WATER_LEVEL_RANGE), 3)
            n["storage_capacity_m3"] = round(random.uniform(*water_network.TOWER_STORAGE_CAPACITY_M3_RANGE), 1)
            n["base_flow"] = round(random.uniform(0.4, 0.8), 3)
        elif ntype == "pipe_junction":
            n["pipe_material"] = random.choice(water_network.JUNCTION_PIPE_MATERIAL_OPTIONS)
            n["pipe_age_years"] = random.uniform(*water_network.JUNCTION_PIPE_AGE_YEARS_RANGE)
            n["pipe_diameter_mm"] = random.randint(*water_network.JUNCTION_PIPE_DIAMETER_MM_RANGE)
            n["pressure"] = round(random.uniform(*water_network.JUNCTION_INITIAL_PRESSURE_RANGE), 3)
            n["base_flow"] = round(random.uniform(0.3, 0.7), 3)

    # ── 3. Scramble Telecom Network ───────────────────────────────────────────
    for n in ta.nodes.values():
        ttype = n.get("tower_type")
        power_range = (2.0, 5.0) if ttype == "ground" else (0.5, 2.0)
        battery_range = (4.0, 8.0) if ttype == "ground" else (2.0, 4.0)
        
        n["power_consumption_kw"] = round(random.uniform(*power_range), 2)
        n["battery"]["capacity_kwh"] = round(random.uniform(*battery_range), 1)
        # Randomise starting charge between 80% and 100%
        n["battery"]["remaining_kwh"] = n["battery"]["capacity_kwh"] * random.uniform(0.8, 1.0)
        
        # Scramble providers
        for p in n.get("providers", []):
            loss_range = (1.0, 3.0) if ttype == "ground" else (0.5, 2.0)
            p["environmental_loss_db"] = round(random.uniform(*loss_range), 2)


# ══════════════════════════════════════════════════════════════════════════════
# STATISTICS
# ══════════════════════════════════════════════════════════════════════════════

def compute_stats(results: list[dict], metric: str) -> dict:
    """Compute mean, std, 95% CI, min, max for a given metric."""
    values = [r[metric] for r in results]
    n = len(values)
    if n == 0:
        return {"mean": 0, "std": 0, "ci_lo": 0, "ci_hi": 0, "min": 0, "max": 0}

    mu = statistics.mean(values)
    if n > 1:
        sd = statistics.stdev(values)
    else:
        sd = 0.0
    se = sd / math.sqrt(n) if n > 0 else 0
    ci_lo = mu - 1.96 * se
    ci_hi = mu + 1.96 * se

    return {
        "mean":  round(mu, 4),
        "std":   round(sd, 4),
        "ci_lo": round(ci_lo, 4),
        "ci_hi": round(ci_hi, 4),
        "min":   round(min(values), 4),
        "max":   round(max(values), 4),
        "p25":   round(sorted(values)[n // 4], 4),
        "p50":   round(sorted(values)[n // 2], 4),
        "p75":   round(sorted(values)[3 * n // 4], 4),
    }


def print_histogram(values: list[float], label: str, bins: int = 10):
    """Print a simple ASCII histogram."""
    if not values:
        return
    lo, hi = min(values), max(values)
    if lo == hi:
        print(f"    {label}: all values = {lo}")
        return

    bin_width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = min(int((v - lo) / bin_width), bins - 1)
        counts[idx] += 1

    max_count = max(counts)
    bar_scale = 40 / max_count if max_count > 0 else 1

    print(f"\n    {BOLD}{label}{RESET}")
    for i in range(bins):
        edge = lo + i * bin_width
        bar = "█" * int(counts[i] * bar_scale)
        print(f"    {edge:8.2f} │{bar} {counts[i]}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="Monte Carlo Simulation Engine")
    parser.add_argument("--runs", type=int, default=1000, help="Number of runs")
    parser.add_argument("--ticks", type=int, default=60, help="Ticks per run")
    parser.add_argument("--fail", type=str, nargs="+", default=["Substation 1"],
                        help="Names or IDs of multiple nodes to fail simultaneously")
    parser.add_argument("--flood", action="store_true",
                        help="Also inject flood at tick 5")
    parser.add_argument("--seed-base", type=int, default=0,
                        help="Base seed (run i uses seed = base + i)")
    args = parser.parse_args()

    # ── Suppress noisy logging ────────────────────────────────────────────────
    logging.basicConfig(level=logging.WARNING)

    # ── Verify graph files ────────────────────────────────────────────────────
    power_json  = "graphs/power.json"
    water_json  = "graphs/water.json"
    telecom_json = "graphs/telecom.json"
    for path in [power_json, water_json, telecom_json]:
        if not os.path.exists(path):
            print(f"{RED}ERROR: {path} not found.{RESET}")
            sys.exit(1)

    # ── Find substation ID ────────────────────────────────────────────────────
    with open(power_json, "r") as f: power_data = json.load(f)
    with open(water_json, "r") as f: water_data = json.load(f)
    with open(telecom_json, "r") as f: telecom_data = json.load(f)

    # Build a master dictionary mapping node names/ids to their Network and ID
    master_lookup = {}
    for n in power_data["nodes"]:
        nid = str(n["node_id"])
        name = n.get("name", nid)
        master_lookup[nid] = {"network": Network.POWER, "id": nid, "name": name}
        master_lookup[name] = {"network": Network.POWER, "id": nid, "name": name}
    
    for n in water_data["nodes"]:
        nid = str(n["node_id"])
        name = n.get("name", nid)
        master_lookup[nid] = {"network": Network.WATER, "id": nid, "name": name}
        master_lookup[name] = {"network": Network.WATER, "id": nid, "name": name}

    for n in telecom_data.get("towers", telecom_data.get("nodes", [])):
        nid = str(n.get("tower_id", n.get("node_id")))
        name = n.get("name", nid)
        master_lookup[nid] = {"network": Network.TELECOM, "id": nid, "name": name}
        master_lookup[name] = {"network": Network.TELECOM, "id": nid, "name": name}

    # Resolve user's chosen targets
    fail_targets = []
    target_names = []
    for q in args.fail:
        if q in master_lookup:
            fail_targets.append(master_lookup[q])
            target_names.append(master_lookup[q]["name"])
        else:
            # try fuzzy matching
            matched = False
            for k, v in master_lookup.items():
                if q.lower() in k.lower():
                    fail_targets.append(v)
                    target_names.append(v["name"])
                    matched = True
                    break
            if not matched:
                print(f"{RED}ERROR: Node '{q}' not found in any network.{RESET}")
                sys.exit(1)

    # remove duplicates
    unique_targets = {t["id"]: t for t in fail_targets}.values()
    fail_targets = list(unique_targets)
    target_names = [t["name"] for t in fail_targets]

    # ── Find flood targets (if --flood) ───────────────────────────────────────
    flood_junction = None
    flood_tower = None
    if args.flood:
        # pick oldest junction
        oldest_age = -1
        for n in water_data["nodes"]:
            if n.get("node_type") == "pipe_junction":
                age = n.get("pipe_age_years", 0)
                if age > oldest_age:
                    oldest_age = age
                    flood_junction = str(n["node_id"])

        for t in telecom_data.get("towers", telecom_data.get("nodes", [])):
            if t.get("tower_type") == "ground":
                flood_tower = str(t.get("tower_id", t.get("node_id")))
                break

    # ── Print header ──────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'═'*70}{RESET}")
    print(f"{BOLD}  Monte Carlo Simulation Engine{RESET}")
    print(f"{'═'*70}")
    print(f"  Fail Targets: {', '.join(target_names)}")
    print(f"  Flood:       {'Yes (tick 5)' if args.flood else 'No'}")
    print(f"  Runs:        {args.runs}")
    print(f"  Ticks/run:   {args.ticks} ({args.ticks * TICK_DURATION_MINUTES:.0f} simulated minutes)")
    print(f"  Seed range:  {args.seed_base} – {args.seed_base + args.runs - 1}")
    print(f"{'═'*70}\n")

    # ── Run simulations ───────────────────────────────────────────────────────
    results = []
    t_start = time.time()

    for i in range(args.runs):
        seed = args.seed_base + i
        metrics = await run_single_simulation(
            run_id=i,
            seed=seed,
            fail_targets=fail_targets,
            flood_junction=flood_junction,
            flood_tower=flood_tower,
            n_ticks=args.ticks,
            power_json=power_json,
            water_json=water_json,
            telecom_json=telecom_json,
        )
        results.append(metrics)

        # Progress bar
        if (i + 1) % 50 == 0 or i == 0 or i == args.runs - 1:
            elapsed = time.time() - t_start
            pct = (i + 1) / args.runs * 100
            eta = elapsed / (i + 1) * (args.runs - i - 1)
            bar_len = 30
            filled = int(bar_len * (i + 1) / args.runs)
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"\r  {bar} {pct:5.1f}%  run {i+1}/{args.runs}  "
                  f"elapsed={elapsed:.1f}s  ETA={eta:.1f}s   ", end="", flush=True)

    elapsed_total = time.time() - t_start
    print(f"\n\n  {GREEN}✓ {args.runs} runs completed in {elapsed_total:.1f}s "
          f"({elapsed_total/args.runs:.3f}s/run){RESET}\n")

    # ── Compute statistics ────────────────────────────────────────────────────
    metrics_to_analyze = [
        ("power_failed",          "Power: Failed Nodes"),
        ("power_degraded",        "Power: Degraded Nodes"),
        ("water_pumps_failed",    "Water: Pumps Failed"),
        ("water_pumps_backup",    "Water: Pumps on Backup"),
        ("water_towers_draining", "Water: Towers Draining"),
        ("water_towers_empty",    "Water: Towers Empty"),
        ("water_pipe_bursts",     "Water: Pipe Bursts"),
        ("water_avg_pressure",    "Water: Avg Junction Pressure"),
        ("water_min_pressure",    "Water: Min Junction Pressure"),
        ("telecom_on_battery",    "Telecom: Towers on Battery"),
        ("telecom_failed",        "Telecom: Towers Failed"),
        ("telecom_avg_batt_pct",  "Telecom: Avg Battery %"),
        ("telecom_min_batt_pct",  "Telecom: Min Battery %"),
        ("total_events",          "Total Cascade Events"),
    ]

    all_stats = {}
    print(f"  {BOLD}{'Metric':<32s}  {'Mean':>8s}  {'Std':>8s}  "
          f"{'95% CI':>17s}  {'Min':>8s}  {'Max':>8s}{RESET}")
    print(f"  {'─'*32}  {'─'*8}  {'─'*8}  {'─'*17}  {'─'*8}  {'─'*8}")

    for key, label in metrics_to_analyze:
        s = compute_stats(results, key)
        all_stats[key] = s
        ci_str = f"[{s['ci_lo']:7.3f}, {s['ci_hi']:7.3f}]"
        print(f"  {label:<32s}  {s['mean']:8.3f}  {s['std']:8.3f}  "
              f"{ci_str:>17s}  {s['min']:8.3f}  {s['max']:8.3f}")

    # ── Key findings ──────────────────────────────────────────────────────────
    print(f"\n{BOLD}  Key Findings{RESET}")
    print(f"  {'─'*50}")

    burst_rate = sum(1 for r in results if r["water_pipe_bursts"] > 0) / len(results) * 100
    print(f"  Pipe burst probability:      {YELLOW}{burst_rate:.1f}%{RESET} of runs")

    telecom_fail_rate = sum(1 for r in results if r["telecom_failed"] > 0) / len(results) * 100
    print(f"  Telecom tower failure rate:  {YELLOW}{telecom_fail_rate:.1f}%{RESET} of runs")

    pump_fail_rate = sum(1 for r in results if r["water_pumps_failed"] > 0) / len(results) * 100
    print(f"  Pump failure rate:           {YELLOW}{pump_fail_rate:.1f}%{RESET} of runs")

    avg_cascade = all_stats["total_events"]["mean"]
    print(f"  Avg cascade events:          {YELLOW}{avg_cascade:.1f}{RESET} events/run")

    # ── Histograms ────────────────────────────────────────────────────────────
    print_histogram([r["water_pipe_bursts"] for r in results],
                    "Pipe Bursts Distribution", bins=6)
    print_histogram([r["telecom_min_batt_pct"] for r in results],
                    "Telecom Min Battery % Distribution", bins=10)
    print_histogram([r["water_avg_pressure"] for r in results],
                    "Water Avg Pressure Distribution", bins=10)

    # ── Save CSV ──────────────────────────────────────────────────────────────
    os.makedirs("data", exist_ok=True)
    csv_path = "data/monte_carlo_results.csv"
    fieldnames = list(results[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\n  {GREEN}✓ Raw data saved: {csv_path} ({len(results)} rows){RESET}")

    # ── Save summary ──────────────────────────────────────────────────────────
    summary_path = "data/monte_carlo_summary.txt"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(f"Monte Carlo Simulation Summary\n")
        f.write(f"{'='*60}\n")
        f.write(f"Fail Targets: {', '.join(target_names)}\n")
        f.write(f"Flood: {'Yes' if args.flood else 'No'}\n")
        f.write(f"Runs: {args.runs}\n")
        f.write(f"Ticks/run: {args.ticks} ({args.ticks * TICK_DURATION_MINUTES:.0f} min)\n")
        f.write(f"Total time: {elapsed_total:.1f}s\n\n")

        f.write(f"{'Metric':<32s}  {'Mean':>8s}  {'Std':>8s}  "
                f"{'CI_Lo':>8s}  {'CI_Hi':>8s}  {'Min':>8s}  {'P25':>8s}  "
                f"{'P50':>8s}  {'P75':>8s}  {'Max':>8s}\n")
        f.write(f"{'-'*120}\n")
        for key, label in metrics_to_analyze:
            s = all_stats[key]
            f.write(f"{label:<32s}  {s['mean']:8.4f}  {s['std']:8.4f}  "
                    f"{s['ci_lo']:8.4f}  {s['ci_hi']:8.4f}  {s['min']:8.4f}  "
                    f"{s['p25']:8.4f}  {s['p50']:8.4f}  {s['p75']:8.4f}  "
                    f"{s['max']:8.4f}\n")

        f.write(f"\nKey Findings:\n")
        f.write(f"  Pipe burst probability:     {burst_rate:.1f}%\n")
        f.write(f"  Telecom failure rate:        {telecom_fail_rate:.1f}%\n")
        f.write(f"  Pump failure rate:           {pump_fail_rate:.1f}%\n")
        f.write(f"  Avg cascade events/run:      {avg_cascade:.1f}\n")

    print(f"  {GREEN}✓ Summary saved:  {summary_path}{RESET}")

    print(f"\n{BOLD}{'═'*70}{RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())

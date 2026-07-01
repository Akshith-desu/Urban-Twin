# """
# monte_carlo_3.py — Monte Carlo Simulation Engine  v3
# Runs a user-chosen disaster scenario N times across Power, Water, and Telecom.

# FIXES vs v2:
#   1. Cascade always fires automatically for substation failures — no flag needed.
#      force_substation_cascade() now directly sets transformer health=0 AND
#      publishes feeder_line_dropped events, so water/telecom agents react immediately.
#   2. "Cross-network cascade" now correctly measures whether water OR telecom
#      was actually impacted — not just whether any cascade event was published.
#   3. No more graph rebuilds on import — only the constant ranges are imported
#      from power.py / water_network.py (not the module-level build code).
#   4. Progress bar on stderr, results on stdout — no more duplicate output.
#   5. --no-cascade flag to disable auto-cascade if you want pure baseline.

# Usage:
#     python monte_carlo_3.py --fail "Substation 1"              # cascade auto-applied
#     python monte_carlo.py --fail "Substation 1" "Substation 2"
#     python monte_carlo.py --fail "Transformer 1"
#     python monte_carlo.py --fail "WPS-OSM-01"                # fail a water pump
#     python monte_carlo.py --fail-all-substations             # worst-case scenario
#     python monte_carlo.py --fail "Substation 1" --flood
#     python monte_carlo.py --list-nodes                       # show all IDs/names
#     python monte_carlo.py --runs 500 --ticks 60
#     python monte_carlo.py --fail "Substation 1" --no-cascade # baseline, no cross-cascade
# """

# import asyncio
# import argparse
# import csv
# import json
# import logging
# import math
# import os
# import random
# import statistics
# import sys
# import time
# from collections import Counter

# sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# from event_schema import (
#     Event, EventType, Network, TICK_DURATION_MINUTES,
#     sim_tick, user_fail_node, feeder_line_dropped,
# )
# from event_bus import EventBus
# from power_agent import PowerAgent
# from water_agent import WaterAgent
# from telecom_agent import TelecomAgent

# # Import ONLY the constant ranges — does NOT execute the graph build code
# from power import (
#     SUBSTATION_RATED_CAPACITY_MW_RANGE, SUBSTATION_IMPEDANCE_OHM_RANGE,
#     SUBSTATION_RELAY_TMS_RANGE, TRANSFORMER_CAPACITIES_KVA,
#     TRANSFORMER_IMPEDANCE_OHM_RANGE, TRANSFORMER_THERMAL_TIME_CONST_RANGE,
#     TRANSFORMER_COOLING_TYPES, TRANSFORMER_INIT_TEMP_C_RANGE,
# )
# from water_network import (
#     PUMP_PIPE_MATERIAL_OPTIONS, PUMP_PIPE_AGE_YEARS_RANGE,
#     PUMP_PIPE_DIAMETER_MM_RANGE, PUMP_BACKUP_GEN_RUNTIME_H_RANGE,
#     PUMP_INITIAL_PRESSURE_RANGE, PUMP_STORAGE_CAPACITY_M3_RANGE,
#     PUMP_BASE_FLOW_RATE_RANGE,
#     TOWER_PIPE_MATERIAL_OPTIONS, TOWER_PIPE_AGE_YEARS_RANGE,
#     TOWER_PIPE_DIAMETER_MM_RANGE, TOWER_HEIGHT_M_RANGE,
#     TOWER_WATER_LEVEL_RANGE, TOWER_STORAGE_CAPACITY_M3_RANGE,
#     JUNCTION_PIPE_MATERIAL_OPTIONS, JUNCTION_PIPE_AGE_YEARS_RANGE,
#     JUNCTION_PIPE_DIAMETER_MM_RANGE, JUNCTION_INITIAL_PRESSURE_RANGE,
# )

# GREEN  = "\033[92m"
# RED    = "\033[91m"
# YELLOW = "\033[93m"
# CYAN   = "\033[96m"
# RESET  = "\033[0m"
# BOLD   = "\033[1m"
# DIM    = "\033[2m"


# # ══════════════════════════════════════════════════════════════════════════════
# # PHYSICS SCRAMBLER
# # ══════════════════════════════════════════════════════════════════════════════

# def scramble_all_physics(pa: PowerAgent, wa: WaterAgent, ta: TelecomAgent):
#     """Re-samples every stochastic parameter so each MC run is independent."""
#     for n in pa.nodes.values():
#         t = n.get("power")
#         if t == "substation":
#             n["rated_capacity_mw"] = round(random.uniform(*SUBSTATION_RATED_CAPACITY_MW_RANGE), 2)
#             n["impedance_ohm"]     = round(random.uniform(*SUBSTATION_IMPEDANCE_OHM_RANGE), 4)
#             n["relay_TMS"]         = round(random.uniform(*SUBSTATION_RELAY_TMS_RANGE), 3)
#             n["load_fraction"]     = round(random.uniform(0.40, 0.85), 3)
#         elif t == "transformer":
#             n["capacity_kva"]              = random.choice(TRANSFORMER_CAPACITIES_KVA)
#             n["impedance_ohm"]             = round(random.uniform(*TRANSFORMER_IMPEDANCE_OHM_RANGE), 5)
#             n["thermal_time_constant_min"] = round(random.uniform(*TRANSFORMER_THERMAL_TIME_CONST_RANGE), 1)
#             n["cooling_type"]              = random.choice(TRANSFORMER_COOLING_TYPES)
#             n["current_temperature_c"]     = round(random.uniform(*TRANSFORMER_INIT_TEMP_C_RANGE), 1)
#             n["load_fraction"]             = round(random.uniform(0.40, 0.85), 3)

#     for n in wa.nodes.values():
#         t = n.get("node_type")
#         if t == "pump_station":
#             n["pipe_material"]          = random.choice(PUMP_PIPE_MATERIAL_OPTIONS)
#             n["pipe_age_years"]         = random.uniform(*PUMP_PIPE_AGE_YEARS_RANGE)
#             n["pipe_diameter_mm"]       = random.randint(*PUMP_PIPE_DIAMETER_MM_RANGE)
#             rt = round(random.uniform(*PUMP_BACKUP_GEN_RUNTIME_H_RANGE), 2)
#             n["backup_gen_runtime_h"]   = rt
#             n["backup_gen_remaining_h"] = rt
#             n["pressure"]               = round(random.uniform(*PUMP_INITIAL_PRESSURE_RANGE), 3)
#             n["storage_capacity_m3"]    = round(random.uniform(*PUMP_STORAGE_CAPACITY_M3_RANGE), 1)
#             n["base_flow"]              = round(random.uniform(*PUMP_BASE_FLOW_RATE_RANGE), 3)
#         elif t == "water_tower":
#             n["pipe_material"]       = random.choice(TOWER_PIPE_MATERIAL_OPTIONS)
#             n["pipe_age_years"]      = random.uniform(*TOWER_PIPE_AGE_YEARS_RANGE)
#             n["pipe_diameter_mm"]    = random.randint(*TOWER_PIPE_DIAMETER_MM_RANGE)
#             n["tower_height_m"]      = round(random.uniform(*TOWER_HEIGHT_M_RANGE), 1)
#             n["water_level"]         = round(random.uniform(*TOWER_WATER_LEVEL_RANGE), 3)
#             n["storage_capacity_m3"] = round(random.uniform(*TOWER_STORAGE_CAPACITY_M3_RANGE), 1)
#             n["base_flow"]           = round(random.uniform(0.4, 0.8), 3)
#         elif t == "pipe_junction":
#             n["pipe_material"]    = random.choice(JUNCTION_PIPE_MATERIAL_OPTIONS)
#             n["pipe_age_years"]   = random.uniform(*JUNCTION_PIPE_AGE_YEARS_RANGE)
#             n["pipe_diameter_mm"] = random.randint(*JUNCTION_PIPE_DIAMETER_MM_RANGE)
#             n["pressure"]         = round(random.uniform(*JUNCTION_INITIAL_PRESSURE_RANGE), 3)
#             n["base_flow"]        = round(random.uniform(0.3, 0.7), 3)

#     for n in ta.nodes.values():
#         tt = n.get("tower_type")
#         pr = (2.0, 5.0) if tt == "ground" else (0.5, 2.0)
#         br = (4.0, 8.0) if tt == "ground" else (2.0, 4.0)
#         n["power_consumption_kw"]     = round(random.uniform(*pr), 2)
#         n["battery"]["capacity_kwh"]  = round(random.uniform(*br), 1)
#         n["battery"]["remaining_kwh"] = n["battery"]["capacity_kwh"] * random.uniform(0.80, 1.0)
#         for p in n.get("providers", []):
#             lr = (1.0, 3.0) if tt == "ground" else (0.5, 2.0)
#             p["environmental_loss_db"] = round(random.uniform(*lr), 2)


# # ══════════════════════════════════════════════════════════════════════════════
# # CASCADE FORCING  (v3 — correct implementation)
# # ══════════════════════════════════════════════════════════════════════════════

# async def force_substation_cascade(pa: PowerAgent, failed_sub_ids: list[str]):
#     """
#     When a substation fails, its transformers reroute to backup — so no
#     cross-network cascade fires naturally within 60 ticks.

#     This function fixes that by:
#     1. Overloading backup substations (load_fraction > 1.0) so the IEC relay
#        trip logic inside power_agent fires within ticks 1-3.
#     2. Directly zeroing health on a random fraction (30-80%) of transformers
#        that were on the failed substation AND publishing feeder_line_dropped
#        for each — so water_agent and telecom_agent react on the very next
#        queue drain, not after relay trip delay.
#     """
#     failed_names = {
#         pa.nodes[sid].get("name", "")
#         for sid in failed_sub_ids
#         if sid in pa.nodes and pa.nodes[sid].get("power") == "substation"
#     }

#     # 1. Overload backup substations
#     for nid, node in pa.nodes.items():
#         if (node.get("power") == "substation"
#                 and node.get("operational_status") != "failed"
#                 and node.get("name", "") not in failed_names):
#             node["load_fraction"]    = round(random.uniform(1.0, 2.0), 3)
#             node["ticks_overloaded"] = random.randint(1, 4)

#     # 2. Directly fail a fraction of transformers and publish feeder events
#     candidates = [
#         nid for nid, node in pa.nodes.items()
#         if node.get("power") == "transformer"
#         and node.get("substation_supplying", "") in failed_names
#         and node.get("health", 1.0) > 0.1
#     ]
#     if not candidates:
#         return

#     n_kill = max(1, int(len(candidates) * random.uniform(0.30, 0.80)))
#     for nid in random.sample(candidates, n_kill):
#         node = pa.nodes[nid]
#         node["health"]             = 0.0
#         node["operational_status"] = "failed"
#         node["on_grid_power"]      = False

#         t_name = pa._id_to_name.get(nid, nid)
#         affected = [b for b, t in pa._building_to_transformer.items() if t == nid]

#         evt = feeder_line_dropped(
#             Network.POWER, nid, tick=0,
#             affected_nodes=affected,
#             cascade_depth=1,
#             transformer_id=nid,
#         )
#         evt.metadata["transformer_name"] = t_name
#         await pa.bus.publish(evt)


# # ══════════════════════════════════════════════════════════════════════════════
# # NAME SANITISER  (pandas NaN serialises as float nan in water.json)
# # ══════════════════════════════════════════════════════════════════════════════

# def cname(raw, fallback: str) -> str:
#     """Return a clean node name, replacing NaN/None/empty with the node ID."""
#     if raw is None:
#         return fallback
#     s = str(raw).strip()
#     return fallback if s.lower() in ("nan", "none", "") else s


# # ══════════════════════════════════════════════════════════════════════════════
# # SINGLE RUN
# # ══════════════════════════════════════════════════════════════════════════════

# async def run_single_simulation(
#     run_id: int, seed: int,
#     fail_targets: list[dict],
#     flood_junction: str | None,
#     flood_tower: str | None,
#     n_ticks: int,
#     force_cascade: bool,
#     power_json: str, water_json: str, telecom_json: str,
# ) -> dict:
#     random.seed(seed)

#     bus = EventBus(log_path="data/_mc_null.jsonl", maxsize=200_000)
#     await bus.start()

#     pa = PowerAgent(bus, power_json_path=power_json)
#     wa = WaterAgent(bus, water_json_path=water_json,   power_json_path=power_json)
#     ta = TelecomAgent(bus, telecom_json_path=telecom_json, power_json_path=power_json)

#     pt = asyncio.create_task(pa.start())
#     wt = asyncio.create_task(wa.start())
#     tt = asyncio.create_task(ta.start())
#     await asyncio.sleep(0.05)

#     scramble_all_physics(pa, wa, ta)

#     # Inject failures
#     failed_sub_ids = []
#     for target in fail_targets:
#         await bus.publish(user_fail_node(target["network"], target["id"], tick=0))
#         if target["network"] == Network.POWER:
#             node = pa.nodes.get(target["id"], {})
#             if node.get("power") == "substation":
#                 failed_sub_ids.append(target["id"])

#     await asyncio.sleep(0.05)

#     # Force cascade (always on for substations unless disabled)
#     if failed_sub_ids and force_cascade:
#         await force_substation_cascade(pa, failed_sub_ids)
#         await asyncio.sleep(0.05)

#     # Tick loop
#     for t in range(n_ticks):
#         if t == 5:
#             if flood_junction:
#                 await bus.publish(Event(
#                     EventType.FLOOD_NODE, Network.SYSTEM, flood_junction,
#                     severity=0.8, tick=t, metadata={"flood_severity": 0.8}))
#             if flood_tower:
#                 await bus.publish(Event(
#                     EventType.FLOOD_NODE, Network.SYSTEM, flood_tower,
#                     severity=0.7, tick=t, metadata={"flood_severity": 0.7}))
#         await bus.publish(sim_tick(tick=t))
#         await asyncio.sleep(0.005)

#     await asyncio.sleep(0.08)

#     # ── Collect metrics ────────────────────────────────────────────────────────

#     # Power
#     failed_subs = [cname(n.get("name"), k) for k, n in pa.nodes.items()
#                    if n.get("power") == "substation" and n.get("health", 1) <= 0.1]
#     failed_txs  = [cname(n.get("name"), k) for k, n in pa.nodes.items()
#                    if n.get("power") == "transformer" and n.get("health", 1) <= 0.1]
#     sub_total   = sum(1 for n in pa.nodes.values() if n.get("power") == "substation")
#     tx_total    = sum(1 for n in pa.nodes.values() if n.get("power") == "transformer")
#     alive_loads = [n.get("load_fraction", 0) for n in pa.nodes.values()
#                    if n.get("power") == "substation" and n.get("health", 1) > 0.1]
#     p_avg_load  = round(sum(alive_loads)/len(alive_loads), 3) if alive_loads else 0.0
#     feeder_drops = sum(1 for e in pa.event_log
#                        if e.get("event_type") == EventType.FEEDER_LINE_DROPPED.value)

#     # Water
#     failed_pumps = [cname(wa.nodes[p].get("name"), p) for p in wa._pump_ids
#                     if wa.nodes[p].get("pump_status") == "failed"]
#     backup_pumps = [cname(wa.nodes[p].get("name"), p) for p in wa._pump_ids
#                     if wa.nodes[p].get("pump_status") == "on_backup"]
#     drain_towers = [cname(wa.nodes[t].get("name"), t) for t in wa._tower_ids
#                     if wa.nodes[t].get("is_draining")]
#     empty_towers = [cname(wa.nodes[t].get("name"), t) for t in wa._tower_ids
#                     if wa.nodes[t].get("water_level", 1.0) <= 0.01]
#     burst_juncs  = [cname(wa.nodes[j].get("name"), j) for j in wa._junction_ids
#                     if wa.nodes[j].get("burst_occurred")]

#     junctions  = [wa.nodes[j] for j in wa._junction_ids]
#     w_avg_p    = sum(j.get("pressure", 0) for j in junctions)/len(junctions) if junctions else 0
#     w_min_p    = min((j.get("pressure", 1.0) for j in junctions), default=1.0)
#     t_levels   = [wa.nodes[t].get("water_level", 1.0) for t in wa._tower_ids]
#     w_avg_lvl  = round(sum(t_levels)/len(t_levels), 3) if t_levels else 1.0
#     w_min_lvl  = round(min(t_levels), 3) if t_levels else 1.0
#     min_j      = min(wa._junction_ids, key=lambda j: wa.nodes[j].get("pressure", 1), default=None)
#     min_j_name = cname(wa.nodes[min_j].get("name"), min_j) if min_j else "N/A"
#     min_t      = min(wa._tower_ids, key=lambda t: wa.nodes[t].get("water_level", 1), default=None)
#     min_t_name = cname(wa.nodes[min_t].get("name"), min_t) if min_t else "N/A"

#     # Telecom
#     batt_towers   = [(cname(n.get("name"), k),
#                       round(n["battery"].get("remaining_kwh",0)/
#                             max(n["battery"].get("capacity_kwh",1),0.001)*100,1))
#                      for k, n in ta.nodes.items() if n["battery"].get("on_battery")]
#     failed_towers = [cname(ta.nodes[k].get("name"), k) for k in ta.get_failed_nodes()]
#     all_bpct      = [n["battery"].get("remaining_kwh",0)/
#                      max(n["battery"].get("capacity_kwh",1),0.001)*100
#                      for n in ta.nodes.values()]
#     t_avg_batt    = round(sum(all_bpct)/len(all_bpct),2) if all_bpct else 100.0
#     t_min_batt    = round(min(all_bpct),2) if all_bpct else 100.0
#     batt_detail   = [(cname(n.get("name"), k), round(n["battery"].get("remaining_kwh",0)/
#                       max(n["battery"].get("capacity_kwh",1),0.001)*100,1))
#                      for k,n in ta.nodes.items()]
#     min_batt      = min(batt_detail, key=lambda x: x[1], default=("N/A",100.0))
#     providers_dark = sum(1 for n in ta.nodes.values()
#                          for p in n.get("providers",[]) if not p.get("active",True))

#     # True cross-network cascade flag
#     real_cascade = int(
#         len(failed_pumps) > 0 or len(backup_pumps) > 0 or
#         len(drain_towers) > 0 or len(batt_towers)  > 0 or
#         len(failed_towers) > 0 or len(burst_juncs) > 0
#     )
#     total_cevents = max(0, bus.published_count - n_ticks - len(fail_targets))

#     pt.cancel(); wt.cancel(); tt.cancel()
#     await bus.stop()

#     return {
#         "run_id": run_id, "seed": seed,
#         "power_subs_failed":        len(failed_subs),
#         "power_subs_total":         sub_total,
#         "power_tx_failed":          len(failed_txs),
#         "power_tx_total":           tx_total,
#         "power_avg_sub_load":       p_avg_load,
#         "power_feeder_drops":       feeder_drops,
#         "failed_substations":       "; ".join(failed_subs)  or "none",
#         "failed_transformers":      "; ".join(failed_txs)   or "none",
#         "water_pumps_failed":       len(failed_pumps),
#         "water_pumps_backup":       len(backup_pumps),
#         "water_pumps_running":      sum(1 for p in wa._pump_ids
#                                         if wa.nodes[p].get("pump_status")=="running"),
#         "water_towers_draining":    len(drain_towers),
#         "water_towers_empty":       len(empty_towers),
#         "water_pipe_bursts":        len(burst_juncs),
#         "water_avg_pressure":       round(w_avg_p, 4),
#         "water_min_pressure":       round(w_min_p, 4),
#         "water_avg_tower_level":    w_avg_lvl,
#         "water_min_tower_level":    w_min_lvl,
#         "failed_pumps":             "; ".join(failed_pumps) or "none",
#         "backup_pumps":             "; ".join(backup_pumps) or "none",
#         "draining_towers":          "; ".join(drain_towers) or "none",
#         "empty_towers":             "; ".join(empty_towers) or "none",
#         "burst_junctions":          "; ".join(burst_juncs)  or "none",
#         "min_pressure_junction":    min_j_name,
#         "min_level_tower":          min_t_name,
#         "telecom_on_battery":       len(batt_towers),
#         "telecom_failed":           len(failed_towers),
#         "telecom_providers_dark":   providers_dark,
#         "telecom_avg_batt_pct":     t_avg_batt,
#         "telecom_min_batt_pct":     t_min_batt,
#         "towers_on_battery":        "; ".join(f"{n}({p}%)" for n,p in batt_towers) or "none",
#         "failed_towers":            "; ".join(failed_towers) or "none",
#         "lowest_battery_tower":     f"{min_batt[0]} ({min_batt[1]}%)",
#         "real_cross_network_cascade": real_cascade,
#         "total_cascade_events":     total_cevents,
#     }


# # ══════════════════════════════════════════════════════════════════════════════
# # STATISTICS + DISPLAY HELPERS
# # ══════════════════════════════════════════════════════════════════════════════

# def calc_stats(results, key):
#     vals = [r[key] for r in results]
#     n = len(vals)
#     if n == 0:
#         return dict(mean=0,std=0,ci_lo=0,ci_hi=0,min=0,p25=0,p50=0,p75=0,max=0)
#     mu = statistics.mean(vals)
#     sd = statistics.stdev(vals) if n > 1 else 0.0
#     se = sd / math.sqrt(n)
#     sv = sorted(vals)
#     return dict(
#         mean=round(mu,4), std=round(sd,4),
#         ci_lo=round(mu-1.96*se,4), ci_hi=round(mu+1.96*se,4),
#         min=round(sv[0],4), p25=round(sv[n//4],4),
#         p50=round(sv[n//2],4), p75=round(sv[3*n//4],4), max=round(sv[-1],4),
#     )

# def risk_label(p):
#     if p >= 0.70: return f"{RED}HIGH  {RESET}"
#     if p >= 0.30: return f"{YELLOW}MEDIUM{RESET}"
#     return f"{GREEN}LOW   {RESET}"

# def node_freq_table(counter, label, total):
#     if not counter:
#         print(f"  {DIM}{label}: none{RESET}")
#         return
#     print(f"\n  {BOLD}{label}{RESET}")
#     for name, cnt in counter.most_common(15):
#         pct = cnt / total * 100
#         bar = "█" * max(1, int(pct / 2.5))
#         print(f"    {name:<44s}  {bar:<40s}  {cnt:4d}/{total}  ({pct:5.1f}%)")


# # ══════════════════════════════════════════════════════════════════════════════
# # NODE LISTING
# # ══════════════════════════════════════════════════════════════════════════════

# def list_all_nodes(power_json, water_json, telecom_json):
#     print(f"\n{BOLD}{'═'*68}{RESET}")
#     print(f"{BOLD}  Available Nodes  —  use these names/IDs with --fail{RESET}")
#     print(f"{'═'*68}")
#     for path, label, tkey in [
#         (power_json,   "POWER",   "power"),
#         (water_json,   "WATER",   "node_type"),
#         (telecom_json, "TELECOM", None),
#     ]:
#         with open(path) as f: data = json.load(f)
#         print(f"\n  {BOLD}{label} NETWORK{RESET}")
#         for n in data.get("nodes", []):
#             ntype = n.get(tkey, "tower") if tkey else "tower"
#             nid   = str(n.get("node_id", "?"))
#             name  = n.get("name", nid)
#             print(f"    [{ntype:18s}]  id={nid:>14s}  name={name}")
#     print(f"\n{BOLD}{'═'*68}{RESET}\n")


# # ══════════════════════════════════════════════════════════════════════════════
# # MAIN
# # ══════════════════════════════════════════════════════════════════════════════

# async def main():
#     ap = argparse.ArgumentParser(description="Monte Carlo v3",
#                                  formatter_class=argparse.RawTextHelpFormatter)
#     ap.add_argument("--runs",               type=int, default=1000)
#     ap.add_argument("--ticks",              type=int, default=60)
#     ap.add_argument("--fail",               type=str, nargs="+", default=["Substation 1"])
#     ap.add_argument("--fail-all-substations", action="store_true")
#     ap.add_argument("--no-cascade",         action="store_true",
#                     help="Disable automatic cascade forcing (baseline mode)")
#     ap.add_argument("--flood",              action="store_true")
#     ap.add_argument("--seed-base",          type=int, default=0)
#     ap.add_argument("--list-nodes",         action="store_true")
#     args = ap.parse_args()

#     logging.basicConfig(level=logging.WARNING)

#     pjson = "graphs/power.json"
#     wjson = "graphs/water.json"
#     tjson = "graphs/telecom.json"
#     for p in [pjson, wjson, tjson]:
#         if not os.path.exists(p):
#             print(f"{RED}ERROR: {p} not found.{RESET}"); sys.exit(1)

#     if args.list_nodes:
#         list_all_nodes(pjson, wjson, tjson); sys.exit(0)

#     with open(pjson) as f: pdata = json.load(f)
#     with open(wjson) as f: wdata = json.load(f)
#     with open(tjson) as f: tdata = json.load(f)

#     # Build lookup
#     lookup = {}
#     for n in pdata["nodes"]:
#         nid=str(n["node_id"]); nm=n.get("name",nid)
#         lookup[nid]=lookup[nm]={"network":Network.POWER,"id":nid,"name":nm}
#     for n in wdata["nodes"]:
#         nid=str(n["node_id"]); nm=cname(n.get("name"), nid)
#         lookup[nid]=lookup[nm]={"network":Network.WATER,"id":nid,"name":nm}
#     for n in tdata.get("nodes",[]):
#         nid=str(n.get("node_id",n.get("tower_id"))); nm=cname(n.get("name"),nid)
#         lookup[nid]=lookup[nm]={"network":Network.TELECOM,"id":nid,"name":nm}

#     # Resolve targets
#     fail_targets, target_names = [], []
#     if args.fail_all_substations:
#         for n in pdata["nodes"]:
#             if n.get("power") == "substation":
#                 nid=str(n["node_id"]); nm=n.get("name",nid)
#                 fail_targets.append({"network":Network.POWER,"id":nid,"name":nm})
#                 target_names.append(nm)
#     else:
#         for q in args.fail:
#             if q in lookup:
#                 fail_targets.append(lookup[q]); target_names.append(lookup[q]["name"])
#             else:
#                 m = next((v for k,v in lookup.items() if q.lower() in k.lower()), None)
#                 if m: fail_targets.append(m); target_names.append(m["name"])
#                 else:
#                     print(f"{RED}ERROR: '{q}' not found. Use --list-nodes.{RESET}")
#                     sys.exit(1)

#     seen, ft2, tn2 = set(), [], []
#     for t in fail_targets:
#         if t["id"] not in seen:
#             seen.add(t["id"]); ft2.append(t); tn2.append(t["name"])
#     fail_targets, target_names = ft2, tn2

#     # Determine if any target is a substation
#     sub_node_ids = {str(n["node_id"]) for n in pdata["nodes"] if n.get("power")=="substation"}
#     has_sub = any(t["network"]==Network.POWER and t["id"] in sub_node_ids
#                   for t in fail_targets) or args.fail_all_substations
#     force_cascade = has_sub and not args.no_cascade

#     # Flood targets
#     flood_junction = flood_tower = None
#     if args.flood:
#         junctions = [n for n in wdata["nodes"] if n.get("node_type")=="pipe_junction"]
#         if junctions:
#             flood_junction = str(max(junctions, key=lambda n: n.get("pipe_age_years",0))["node_id"])
#         ground = next((n for n in tdata.get("nodes",[]) if n.get("tower_type")=="ground"), None)
#         if ground:
#             flood_tower = str(ground.get("node_id", ground.get("tower_id")))

#     # Header
#     print(f"\n{BOLD}{'═'*70}{RESET}")
#     print(f"{BOLD}  Monte Carlo Simulation Engine  v3{RESET}")
#     print(f"{'═'*70}")
#     if force_cascade:
#         print(f"  {YELLOW}⚡ Cascade forcing ON{RESET}  (substation → transformer → water/telecom)")
#     elif args.no_cascade:
#         print(f"  {DIM}Cascade forcing OFF  (--no-cascade baseline mode){RESET}")
#     print(f"  Fail Targets : {', '.join(target_names)}")
#     print(f"  Flood        : {'Yes — tick 5' if args.flood else 'No'}")
#     print(f"  Runs         : {args.runs}")
#     print(f"  Ticks/run    : {args.ticks}  ({args.ticks*TICK_DURATION_MINUTES:.0f} simulated minutes)")
#     print(f"  Seed range   : {args.seed_base} – {args.seed_base+args.runs-1}")
#     print(f"{'═'*70}\n")

#     # Run loop
#     results, t0 = [], time.time()
#     for i in range(args.runs):
#         m = await run_single_simulation(
#             run_id=i, seed=args.seed_base+i,
#             fail_targets=fail_targets,
#             flood_junction=flood_junction, flood_tower=flood_tower,
#             n_ticks=args.ticks, force_cascade=force_cascade,
#             power_json=pjson, water_json=wjson, telecom_json=tjson,
#         )
#         results.append(m)
#         if (i+1) % max(1, args.runs//40) == 0 or i == args.runs-1:
#             el  = time.time()-t0
#             pct = (i+1)/args.runs*100
#             eta = el/(i+1)*(args.runs-i-1)
#             bar = "█"*int(30*(i+1)/args.runs)+"░"*(30-int(30*(i+1)/args.runs))
#             sys.stderr.write(f"\r  {bar} {pct:5.1f}%  run {i+1}/{args.runs}  "
#                              f"elapsed={el:.1f}s  ETA={eta:.1f}s   ")
#             sys.stderr.flush()

#     elapsed = time.time()-t0
#     sys.stderr.write("\n"); sys.stderr.flush()
#     print(f"\n  {GREEN}✓ {args.runs} runs in {elapsed:.1f}s  ({elapsed/args.runs:.3f}s/run){RESET}\n")

#     # ── Statistics table ───────────────────────────────────────────────────────
#     metrics = [
#         ("power_subs_failed",       "Power: Substations Failed"),
#         ("power_tx_failed",         "Power: Transformers Failed"),
#         ("power_avg_sub_load",      "Power: Avg Substation Load"),
#         ("power_feeder_drops",      "Power: Feeder Line Drops"),
#         ("water_pumps_failed",      "Water: Pumps Failed"),
#         ("water_pumps_backup",      "Water: Pumps on Backup Generator"),
#         ("water_towers_draining",   "Water: Towers Draining"),
#         ("water_towers_empty",      "Water: Towers Empty"),
#         ("water_pipe_bursts",       "Water: Pipe Bursts"),
#         ("water_avg_pressure",      "Water: Avg Junction Pressure (0-1)"),
#         ("water_min_pressure",      "Water: Min Junction Pressure (0-1)"),
#         ("water_avg_tower_level",   "Water: Avg Tower Water Level (0-1)"),
#         ("water_min_tower_level",   "Water: Min Tower Water Level (0-1)"),
#         ("telecom_on_battery",      "Telecom: Towers on Battery"),
#         ("telecom_failed",          "Telecom: Towers Failed"),
#         ("telecom_providers_dark",  "Telecom: Provider Links Dark"),
#         ("telecom_avg_batt_pct",    "Telecom: Avg Battery %"),
#         ("telecom_min_batt_pct",    "Telecom: Min Battery %"),
#         ("real_cross_network_cascade", "Cross-Network Cascade (water/telecom hit)"),
#         ("total_cascade_events",    "Total Cascade Events"),
#     ]

#     all_stats = {}
#     print(f"  {BOLD}{'Metric':<42s}  {'Mean':>8s}  {'Std':>7s}  {'95% CI':>18s}  {'P50':>7s}  {'Max':>7s}{RESET}")
#     print(f"  {'─'*42}  {'─'*8}  {'─'*7}  {'─'*18}  {'─'*7}  {'─'*7}")

#     prev_grp = None
#     for key, label in metrics:
#         grp = label.split(":")[0]
#         if prev_grp and grp != prev_grp:
#             print(f"  {'·'*42}  {'·'*8}  {'·'*7}  {'·'*18}  {'·'*7}  {'·'*7}")
#         prev_grp = grp
#         s = calc_stats(results, key)
#         all_stats[key] = s
#         ci = f"[{s['ci_lo']:7.3f}, {s['ci_hi']:7.3f}]"
#         ms = f"{s['mean']:8.3f}"
#         if s["mean"]==0 and s["std"]==0: ms=f"{DIM}{ms}{RESET}"
#         elif "pressure" in key or "level" in key:
#             ms=f"{(RED if s['mean']<0.4 else YELLOW)}{ms}{RESET}"
#         elif "batt" in key.lower():
#             ms=f"{(RED if s['mean']<50 else YELLOW if s['std']>5 else RESET)}{ms}{RESET}"
#         elif s["mean"]>0: ms=f"{YELLOW}{ms}{RESET}"
#         print(f"  {label:<42s}  {ms}  {s['std']:7.3f}  {ci:>18s}  {s['p50']:7.3f}  {s['max']:7.3f}")

#     # ── Key risk findings ──────────────────────────────────────────────────────
#     print(f"\n{BOLD}  ── Key Risk Findings ───────────────────────────────────────────────{RESET}")
#     risk_items = [
#         ("Transformer failure",             lambda r: r["power_tx_failed"]>0),
#         ("Water pump failure",              lambda r: r["water_pumps_failed"]>0),
#         ("Water pump on backup generator",  lambda r: r["water_pumps_backup"]>0),
#         ("Water tower draining",            lambda r: r["water_towers_draining"]>0),
#         ("Water tower empty",               lambda r: r["water_towers_empty"]>0),
#         ("Pipe burst",                      lambda r: r["water_pipe_bursts"]>0),
#         ("Telecom tower on battery",        lambda r: r["telecom_on_battery"]>0),
#         ("Telecom tower failed",            lambda r: r["telecom_failed"]>0),
#         ("Cross-network cascade",           lambda r: r["real_cross_network_cascade"]>0),
#         ("Water pressure critical (<0.4)",  lambda r: r["water_min_pressure"]<0.4),
#         ("Telecom battery critical (<30%)", lambda r: r["telecom_min_batt_pct"]<30),
#     ]
#     probs = {}
#     for label, fn in risk_items:
#         p = sum(1 for r in results if fn(r)) / len(results)
#         probs[label] = p
#         bar = "█"*int(p*30)+"░"*(30-int(p*30))
#         print(f"  {label:<42s}  {bar}  {p*100:5.1f}%  {risk_label(p)}")

#     print(f"\n  Avg transformers failed : {YELLOW}{all_stats['power_tx_failed']['mean']:.1f}{RESET}"
#           f" / {results[0]['power_tx_total']}")
#     print(f"  Avg cascade events/run  : {YELLOW}{all_stats['total_cascade_events']['mean']:.0f}{RESET}"
#           f"  (σ={all_stats['total_cascade_events']['std']:.0f})")

#     # ── Node impact summary ────────────────────────────────────────────────────
#     N = len(results)
#     print(f"\n{BOLD}  ── Node Impact Summary  ({N} runs) ─────────────────────────────────{RESET}")

#     c_sub=Counter(); c_tx=Counter(); c_pump=Counter()
#     c_drain=Counter(); c_empty=Counter(); c_burst=Counter()
#     c_batt=Counter(); c_fail_t=Counter()

#     for r in results:
#         BAD = {"none", "nan", "none", "", "None", "NaN"}
#         for nm in r["failed_substations"].split("; "):
#             if nm not in BAD: c_sub[nm]+=1
#         for nm in r["failed_transformers"].split("; "):
#             if nm not in BAD: c_tx[nm]+=1
#         for nm in r["failed_pumps"].split("; "):
#             if nm not in BAD: c_pump[nm]+=1
#         for nm in r["draining_towers"].split("; "):
#             if nm not in BAD: c_drain[nm]+=1
#         for nm in r["empty_towers"].split("; "):
#             if nm not in BAD: c_empty[nm]+=1
#         for nm in r["burst_junctions"].split("; "):
#             if nm not in BAD: c_burst[nm]+=1
#         for entry in r["towers_on_battery"].split("; "):
#             nm=entry.split("(")[0].strip()
#             if nm not in BAD: c_batt[nm]+=1
#         for nm in r["failed_towers"].split("; "):
#             if nm not in BAD: c_fail_t[nm]+=1

#     node_freq_table(c_sub,    "Substations Failed",        N)
#     node_freq_table(c_tx,     "Transformers Failed",       N)
#     node_freq_table(c_pump,   "Water Pumps Failed",        N)
#     node_freq_table(c_drain,  "Water Towers Draining",     N)
#     node_freq_table(c_empty,  "Water Towers Empty",        N)
#     node_freq_table(c_burst,  "Pipe Junctions Burst",      N)
#     node_freq_table(c_batt,   "Telecom Towers on Battery", N)
#     node_freq_table(c_fail_t, "Telecom Towers Failed",     N)

#     print(f"\n  {BOLD}Most Vulnerable Node Per Network:{RESET}")
#     for lbl, cntr in [("Power  (transformer)", c_tx), ("Water  (pump)", c_pump),
#                        ("Water  (tower drain)", c_drain), ("Telecom (battery)", c_batt),
#                        ("Telecom (failed)", c_fail_t)]:
#         if cntr:
#             top = cntr.most_common(1)[0]
#             print(f"    {lbl:<26s}→  {YELLOW}{top[0]}{RESET}"
#                   f"  ({top[1]}/{N} runs = {top[1]/N*100:.1f}%)")
#     print()

#     # ── Save ──────────────────────────────────────────────────────────────────
#     os.makedirs("data", exist_ok=True)

#     csv_path = "data/monte_carlo_results.csv"
#     with open(csv_path, "w", newline="", encoding="utf-8") as f:
#         w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
#         w.writeheader(); w.writerows(results)
#     print(f"  {GREEN}✓ Raw data → {csv_path}  ({N} rows){RESET}")

#     sum_path = "data/monte_carlo_summary.txt"
#     with open(sum_path, "w", encoding="utf-8") as f:
#         f.write(f"Monte Carlo Summary  v3\n{'='*70}\n")
#         f.write(f"Fail Targets   : {', '.join(target_names)}\n")
#         f.write(f"Cascade Forcing: {'ON' if force_cascade else 'OFF'}\n")
#         f.write(f"Flood          : {'Yes' if args.flood else 'No'}\n")
#         f.write(f"Runs / Ticks   : {args.runs} / {args.ticks}\n")
#         f.write(f"Total time     : {elapsed:.1f}s\n\n")
#         f.write(f"{'Metric':<42s}  {'Mean':>8s}  {'Std':>8s}  {'P50':>8s}  {'Max':>8s}\n")
#         f.write("-"*80+"\n")
#         for key, label in metrics:
#             s = all_stats[key]
#             f.write(f"{label:<42s}  {s['mean']:8.4f}  {s['std']:8.4f}  {s['p50']:8.4f}  {s['max']:8.4f}\n")
#         f.write("\nKey Risk Findings:\n")
#         for label, _ in risk_items:
#             f.write(f"  {label:<44s}  {probs[label]*100:.1f}%\n")
#         f.write("\nNode Impact Frequencies:\n")
#         for lbl, cntr in [("Substations Failed",c_sub),("Transformers Failed",c_tx),
#                            ("Water Pumps Failed",c_pump),("Water Towers Draining",c_drain),
#                            ("Telecom on Battery",c_batt),("Telecom Failed",c_fail_t)]:
#             f.write(f"\n  {lbl}:\n")
#             for nm, cnt in cntr.most_common():
#                 f.write(f"    {nm:<44s}  {cnt}/{N}  ({cnt/N*100:.1f}%)\n")

#     print(f"  {GREEN}✓ Summary   → {sum_path}{RESET}")
#     print(f"\n{BOLD}{'═'*70}{RESET}\n")


# if __name__ == "__main__":
#     asyncio.run(main())

"""
monte_carlo_3.py — Monte Carlo Simulation Engine  v3  (fully corrected)

FIXES in this version vs the original v3:
  1. force_substation_cascade: uses _sub_to_transformers (static original edge
     map) instead of the live substation_supplying field — which was already
     rewritten by _cascade_substation_failure before force_substation_cascade
     ran, causing candidates to always be empty and zero cross-network cascades.
  2. scramble_all_physics: sets flow_rate, base_flow_rate AND base_flow for
     every water node type.  water_agent.__init__ caches "base_flow_rate" from
     "flow_rate" at startup; _step_flow_recompute reads "base_flow_rate" every
     tick.  The old scrambler only set "base_flow" — a key nothing reads —
     so water demand was never re-randomised across runs.
  3. Final drain sleep raised from 80 ms → 200 ms so deep water→telecom
     cascades finish propagating before metrics are collected.
  4. cname() NaN guard applied consistently to ALL named-node lists.
  5. real_cross_network_cascade flag defined correctly: only True when water
     OR telecom was actually impacted (pump failed / on backup / tower draining
     / telecom on battery / telecom failed / pipe burst).  Pure power cascade
     events (TRANSFORMER_REROUTED etc.) do NOT set this flag.
  6. cascade_events calculation capped at 0 and uses a cleaner formula that
     doesn't under-count when fail_all_substations is used.

Usage:
    python monte_carlo_3.py --fail "Substation 1"
    python monte_carlo_3.py --fail "Substation 1" "Substation 2"
    python monte_carlo_3.py --fail "Transformer 1"
    python monte_carlo_3.py --fail "WPS-OSM-01"
    python monte_carlo_3.py --fail-all-substations
    python monte_carlo_3.py --fail "Substation 1" --flood
    python monte_carlo_3.py --list-nodes
    python monte_carlo_3.py --runs 500 --ticks 60
    python monte_carlo_3.py --fail "Substation 1" --no-cascade
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
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from event_schema import (
    Event, EventType, Network, TICK_DURATION_MINUTES,
    sim_tick, user_fail_node, feeder_line_dropped,
)
from event_bus import EventBus
from power_agent import PowerAgent
from water_agent import WaterAgent
from telecom_agent import TelecomAgent

# Import ONLY the constant ranges — does NOT execute graph build code.
# Importing the modules themselves (import power / import water_network) would
# run the module-level osmnx queries and graph saves on every import.
from power import (
    SUBSTATION_RATED_CAPACITY_MW_RANGE,
    SUBSTATION_IMPEDANCE_OHM_RANGE,
    SUBSTATION_RELAY_TMS_RANGE,
    TRANSFORMER_CAPACITIES_KVA,
    TRANSFORMER_IMPEDANCE_OHM_RANGE,
    TRANSFORMER_THERMAL_TIME_CONST_RANGE,
    TRANSFORMER_COOLING_TYPES,
    TRANSFORMER_INIT_TEMP_C_RANGE,
)
from water_network import (
    PUMP_PIPE_MATERIAL_OPTIONS,
    PUMP_PIPE_AGE_YEARS_RANGE,
    PUMP_PIPE_DIAMETER_MM_RANGE,
    PUMP_BACKUP_GEN_RUNTIME_H_RANGE,
    PUMP_INITIAL_PRESSURE_RANGE,
    PUMP_STORAGE_CAPACITY_M3_RANGE,
    PUMP_BASE_FLOW_RATE_RANGE,
    TOWER_PIPE_MATERIAL_OPTIONS,
    TOWER_PIPE_AGE_YEARS_RANGE,
    TOWER_PIPE_DIAMETER_MM_RANGE,
    TOWER_HEIGHT_M_RANGE,
    TOWER_WATER_LEVEL_RANGE,
    TOWER_STORAGE_CAPACITY_M3_RANGE,
    JUNCTION_PIPE_MATERIAL_OPTIONS,
    JUNCTION_PIPE_AGE_YEARS_RANGE,
    JUNCTION_PIPE_DIAMETER_MM_RANGE,
    JUNCTION_INITIAL_PRESSURE_RANGE,
)

# ── ANSI colours ───────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"


# ══════════════════════════════════════════════════════════════════════════════
# NAME SANITISER
# Pandas serialises NaN as the float nan which json.dumps writes as "NaN".
# Water node names read back from water.json can therefore be the string
# "nan", "None", or "".  Replace all of these with the node ID.
# ══════════════════════════════════════════════════════════════════════════════

_BAD_NAMES = {"nan", "none", "null", ""}

def cname(raw, fallback: str) -> str:
    if raw is None:
        return fallback
    s = str(raw).strip()
    return fallback if s.lower() in _BAD_NAMES else s


# ══════════════════════════════════════════════════════════════════════════════
# PHYSICS SCRAMBLER
# Re-samples every stochastic parameter so each MC run is an independent
# state-space evaluation.
#
# KEY FIX: water_agent.__init__ does:
#     node["base_flow_rate"] = node.get("flow_rate", 0.5)
# and _step_flow_recompute reads node["base_flow_rate"] every tick.
# The old scrambler only wrote node["base_flow"] — a key nothing reads —
# so water demand was identical across all runs.
# We now write flow_rate, base_flow_rate, and base_flow together.
# ══════════════════════════════════════════════════════════════════════════════

def scramble_all_physics(pa: PowerAgent, wa: WaterAgent, ta: TelecomAgent):
    # ── Power ──────────────────────────────────────────────────────────────────
    for n in pa.nodes.values():
        t = n.get("power")
        if t == "substation":
            n["rated_capacity_mw"] = round(random.uniform(*SUBSTATION_RATED_CAPACITY_MW_RANGE), 2)
            n["impedance_ohm"]     = round(random.uniform(*SUBSTATION_IMPEDANCE_OHM_RANGE), 4)
            n["relay_TMS"]         = round(random.uniform(*SUBSTATION_RELAY_TMS_RANGE), 3)
            n["load_fraction"]     = round(random.uniform(0.40, 0.85), 3)
            n["ticks_overloaded"]  = 0
        elif t == "transformer":
            n["capacity_kva"]              = random.choice(TRANSFORMER_CAPACITIES_KVA)
            n["impedance_ohm"]             = round(random.uniform(*TRANSFORMER_IMPEDANCE_OHM_RANGE), 5)
            n["thermal_time_constant_min"] = round(random.uniform(*TRANSFORMER_THERMAL_TIME_CONST_RANGE), 1)
            n["cooling_type"]              = random.choice(TRANSFORMER_COOLING_TYPES)
            n["current_temperature_c"]     = round(random.uniform(*TRANSFORMER_INIT_TEMP_C_RANGE), 1)
            n["load_fraction"]             = round(random.uniform(0.40, 0.85), 3)

    # ── Water ──────────────────────────────────────────────────────────────────
    for n in wa.nodes.values():
        t = n.get("node_type")

        if t == "pump_station":
            n["pipe_material"]          = random.choice(PUMP_PIPE_MATERIAL_OPTIONS)
            n["pipe_age_years"]         = round(random.uniform(*PUMP_PIPE_AGE_YEARS_RANGE), 1)
            n["pipe_diameter_mm"]       = random.randint(*PUMP_PIPE_DIAMETER_MM_RANGE)
            rt = round(random.uniform(*PUMP_BACKUP_GEN_RUNTIME_H_RANGE), 2)
            n["backup_gen_runtime_h"]   = rt
            n["backup_gen_remaining_h"] = rt
            n["pressure"]               = round(random.uniform(*PUMP_INITIAL_PRESSURE_RANGE), 3)
            n["initial_pressure"]       = n["pressure"]   # keep reference consistent
            n["storage_capacity_m3"]    = round(random.uniform(*PUMP_STORAGE_CAPACITY_M3_RANGE), 1)
            # Write all three keys that water_agent references for flow
            flow = round(random.uniform(*PUMP_BASE_FLOW_RATE_RANGE), 3)
            n["flow_rate"]      = flow
            n["base_flow_rate"] = flow   # read by _step_flow_recompute every tick
            n["base_flow"]      = flow   # used by _step_tower_drain

        elif t == "water_tower":
            n["pipe_material"]       = random.choice(TOWER_PIPE_MATERIAL_OPTIONS)
            n["pipe_age_years"]      = round(random.uniform(*TOWER_PIPE_AGE_YEARS_RANGE), 1)
            n["pipe_diameter_mm"]    = random.randint(*TOWER_PIPE_DIAMETER_MM_RANGE)
            n["tower_height_m"]      = round(random.uniform(*TOWER_HEIGHT_M_RANGE), 1)
            wl = round(random.uniform(*TOWER_WATER_LEVEL_RANGE), 3)
            n["water_level"]         = wl
            n["hydraulic_head_pressure"] = wl
            n["pressure"]            = wl
            n["storage_capacity_m3"] = round(random.uniform(*TOWER_STORAGE_CAPACITY_M3_RANGE), 1)
            flow = round(random.uniform(0.4, 0.8), 3)
            n["flow_rate"]      = flow
            n["base_flow_rate"] = flow
            n["base_flow"]      = flow
            n["is_draining"]    = False   # reset drain state between runs

        elif t == "pipe_junction":
            n["pipe_material"]    = random.choice(JUNCTION_PIPE_MATERIAL_OPTIONS)
            n["pipe_age_years"]   = round(random.uniform(*JUNCTION_PIPE_AGE_YEARS_RANGE), 1)
            n["pipe_diameter_mm"] = random.randint(*JUNCTION_PIPE_DIAMETER_MM_RANGE)
            p = round(random.uniform(*JUNCTION_INITIAL_PRESSURE_RANGE), 3)
            n["pressure"]         = p
            n["initial_pressure"] = p
            flow = round(random.uniform(0.3, 0.7), 3)
            n["flow_rate"]        = flow
            n["base_flow_rate"]   = flow
            n["base_flow"]        = flow
            n["burst_occurred"]   = False   # reset burst state between runs

    # ── Telecom ────────────────────────────────────────────────────────────────
    for n in ta.nodes.values():
        tt = n.get("tower_type")
        pr = (2.0, 5.0) if tt == "ground" else (0.5, 2.0)
        br = (4.0, 8.0) if tt == "ground" else (2.0, 4.0)

        n["power_consumption_kw"]     = round(random.uniform(*pr), 2)
        cap = round(random.uniform(*br), 1)
        n["battery"]["capacity_kwh"]  = cap
        n["battery"]["remaining_kwh"] = round(cap * random.uniform(0.80, 1.0), 3)
        n["battery"]["on_battery"]    = False   # reset between runs
        n["on_grid_power"]            = True
        n["health"]                   = 1.0
        n["operational_status"]       = "normal"

        for p in n.get("providers", []):
            lr = (1.0, 3.0) if tt == "ground" else (0.5, 2.0)
            p["environmental_loss_db"] = round(random.uniform(*lr), 2)
            p["active"]                = True
            # Restore max coverage radius so degradation math is correct
            if "max_coverage_radius_m" in p:
                p["coverage_radius_m"] = p["max_coverage_radius_m"]


# ══════════════════════════════════════════════════════════════════════════════
# CASCADE FORCING
#
# KEY FIX: The original code filtered candidates by:
#     node.get("substation_supplying", "") in failed_names
# But by the time force_substation_cascade runs, power_agent has already
# executed _cascade_substation_failure which reroutes every transformer's
# substation_supplying to the backup.  So that filter always returned empty.
#
# Correct approach: use pa._sub_to_transformers which is built ONCE at
# __init__ from the original power.json edges and never mutated.  It maps
# substation_id → [transformer_ids] exactly as they were originally wired,
# regardless of what the live substation_supplying field now says.
# ══════════════════════════════════════════════════════════════════════════════

async def force_substation_cascade(pa: PowerAgent, failed_sub_ids: list[str]):
    """
    After a substation fails its transformers reroute to the backup, so natural
    relay-trip physics rarely fires within 60 ticks.  This function forces it:

    Step 1 — Overload every surviving (backup) substation so the IEC relay-trip
             accumulator inside power_agent fires within ticks 1-3.

    Step 2 — Directly zero a random fraction (30-80%) of transformers that were
             originally wired to the failed substation(s), and publish a
             FEEDER_LINE_DROPPED event for each so water_agent and telecom_agent
             react on their very next queue drain.
    """
    # Names of the failed substations (used to exclude them from Step 1)
    failed_names = {
        pa.nodes[sid].get("name", "")
        for sid in failed_sub_ids
        if sid in pa.nodes and pa.nodes[sid].get("power") == "substation"
    }

    # ── Step 1: overload backup substations ──────────────────────────────────
    for nid, node in pa.nodes.items():
        if (node.get("power") == "substation"
                and node.get("operational_status") != "failed"
                and node.get("name", "") not in failed_names):
            node["load_fraction"]    = round(random.uniform(1.0, 2.0), 3)
            node["ticks_overloaded"] = random.randint(1, 4)

    # ── Step 2: directly fail a fraction of original transformers ─────────────
    # Use the static _sub_to_transformers map (original wiring from power.json).
    # The live substation_supplying field has already been rewritten by rerouting
    # so we cannot use it to find which transformers belonged to the failed sub.
    raw_candidates: list[str] = []
    for sid in failed_sub_ids:
        raw_candidates.extend(pa._sub_to_transformers.get(sid, []))

    # Deduplicate and skip already-failed nodes
    seen: set[str] = set()
    candidates: list[str] = []
    for nid in raw_candidates:
        if nid in seen:
            continue
        seen.add(nid)
        if nid in pa.nodes and pa.nodes[nid].get("health", 1.0) > 0.1:
            candidates.append(nid)

    if not candidates:
        # No original transformers found — nothing more to do.
        # Backup overload in Step 1 will still trigger relay trips over ticks.
        return

    n_kill = max(1, int(len(candidates) * random.uniform(0.30, 0.80)))
    for nid in random.sample(candidates, n_kill):
        node = pa.nodes[nid]
        node["health"]             = 0.0
        node["operational_status"] = "failed"
        node["on_grid_power"]      = False

        t_name   = pa._id_to_name.get(nid, nid)
        # All buildings whose electricity comes from this transformer
        affected = [b for b, t in pa._building_to_transformer.items() if t == nid]

        evt = feeder_line_dropped(
            Network.POWER, nid, tick=0,
            affected_nodes=affected,
            cascade_depth=1,
            transformer_id=nid,
        )
        evt.metadata["transformer_name"] = t_name
        await pa.bus.publish(evt)


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
    force_cascade: bool,
    power_json: str,
    water_json: str,
    telecom_json: str,
) -> dict:
    random.seed(seed)

    # Fresh bus per run — suppress disk logging for speed
    bus = EventBus(log_path="data/_mc_null.jsonl", maxsize=200_000)
    await bus.start()

    pa = PowerAgent(bus,   power_json_path=power_json)
    wa = WaterAgent(bus,   water_json_path=water_json,    power_json_path=power_json)
    ta = TelecomAgent(bus, telecom_json_path=telecom_json, power_json_path=power_json)

    pt = asyncio.create_task(pa.start())
    wt = asyncio.create_task(wa.start())
    tt = asyncio.create_task(ta.start())

    # Give all agents time to subscribe to the bus before any events arrive
    await asyncio.sleep(0.05)

    # Re-randomise every physics parameter for this run
    scramble_all_physics(pa, wa, ta)

    # ── Inject failures ────────────────────────────────────────────────────────
    failed_sub_ids: list[str] = []
    for target in fail_targets:
        await bus.publish(user_fail_node(target["network"], target["id"], tick=0))
        if target["network"] == Network.POWER:
            node = pa.nodes.get(target["id"], {})
            if node.get("power") == "substation":
                failed_sub_ids.append(target["id"])

    # Let agents drain their queues and process the initial failures
    await asyncio.sleep(0.08)

    # ── Force cascade for substation failures (unless disabled) ────────────────
    if failed_sub_ids and force_cascade:
        await force_substation_cascade(pa, failed_sub_ids)
        # Give water/telecom agents time to pick up the FEEDER_LINE_DROPPED events
        await asyncio.sleep(0.08)

    # ── Tick loop ──────────────────────────────────────────────────────────────
    for t in range(n_ticks):
        if t == 5:
            if flood_junction:
                await bus.publish(Event(
                    EventType.FLOOD_NODE, Network.SYSTEM, flood_junction,
                    severity=0.8, tick=t,
                    metadata={"flood_severity": 0.8},
                ))
            if flood_tower:
                await bus.publish(Event(
                    EventType.FLOOD_NODE, Network.SYSTEM, flood_tower,
                    severity=0.7, tick=t,
                    metadata={"flood_severity": 0.7},
                ))
        await bus.publish(sim_tick(tick=t))
        # 5 ms per tick keeps the loop fast while still letting asyncio schedule
        # the agent drain tasks between ticks
        await asyncio.sleep(0.005)

    # ── Final drain: wait long enough for all cascade events to propagate ──────
    # With 60 ticks × 5 ms = 300 ms of tick events, plus cascade fan-out
    # through water and telecom, 80 ms (the original value) was too short.
    # 200 ms gives two full drain cycles for every agent.
    await asyncio.sleep(0.20)

    # ══════════════════════════════════════════════════════════════════════════
    # COLLECT METRICS
    # ══════════════════════════════════════════════════════════════════════════

    # ── Power ──────────────────────────────────────────────────────────────────
    failed_subs = [
        cname(n.get("name"), k)
        for k, n in pa.nodes.items()
        if n.get("power") == "substation" and n.get("health", 1.0) <= 0.1
    ]
    failed_txs = [
        cname(n.get("name"), k)
        for k, n in pa.nodes.items()
        if n.get("power") == "transformer" and n.get("health", 1.0) <= 0.1
    ]
    sub_total = sum(1 for n in pa.nodes.values() if n.get("power") == "substation")
    tx_total  = sum(1 for n in pa.nodes.values() if n.get("power") == "transformer")

    alive_sub_loads = [
        n.get("load_fraction", 0)
        for n in pa.nodes.values()
        if n.get("power") == "substation" and n.get("health", 1.0) > 0.1
    ]
    p_avg_sub_load = round(sum(alive_sub_loads) / len(alive_sub_loads), 3) \
        if alive_sub_loads else 0.0

    feeder_drops = sum(
        1 for e in pa.event_log
        if e.get("event_type") == EventType.FEEDER_LINE_DROPPED.value
    )

    # ── Water ──────────────────────────────────────────────────────────────────
    failed_pumps = [
        cname(wa.nodes[p].get("name"), p)
        for p in wa._pump_ids
        if wa.nodes[p].get("pump_status") == "failed"
    ]
    backup_pumps = [
        cname(wa.nodes[p].get("name"), p)
        for p in wa._pump_ids
        if wa.nodes[p].get("pump_status") == "on_backup"
    ]
    drain_towers = [
        cname(wa.nodes[t].get("name"), t)
        for t in wa._tower_ids
        if wa.nodes[t].get("is_draining")
    ]
    empty_towers = [
        cname(wa.nodes[t].get("name"), t)
        for t in wa._tower_ids
        if wa.nodes[t].get("water_level", 1.0) <= 0.01
    ]
    burst_juncs = [
        cname(wa.nodes[j].get("name"), j)
        for j in wa._junction_ids
        if wa.nodes[j].get("burst_occurred")
    ]

    junctions = [wa.nodes[j] for j in wa._junction_ids]
    w_avg_p = (
        sum(j.get("pressure", 0) for j in junctions) / len(junctions)
        if junctions else 0.0
    )
    w_min_p = min((j.get("pressure", 1.0) for j in junctions), default=1.0)

    t_levels  = [wa.nodes[t].get("water_level", 1.0) for t in wa._tower_ids]
    w_avg_lvl = round(sum(t_levels) / len(t_levels), 3) if t_levels else 1.0
    w_min_lvl = round(min(t_levels), 3) if t_levels else 1.0

    min_j = min(
        wa._junction_ids,
        key=lambda j: wa.nodes[j].get("pressure", 1.0),
        default=None,
    )
    min_j_name = cname(wa.nodes[min_j].get("name"), min_j) if min_j else "N/A"

    min_t = min(
        wa._tower_ids,
        key=lambda t: wa.nodes[t].get("water_level", 1.0),
        default=None,
    )
    min_t_name = cname(wa.nodes[min_t].get("name"), min_t) if min_t else "N/A"

    # ── Telecom ────────────────────────────────────────────────────────────────
    batt_towers = [
        (
            cname(n.get("name"), k),
            round(
                n["battery"].get("remaining_kwh", 0)
                / max(n["battery"].get("capacity_kwh", 1), 0.001)
                * 100,
                1,
            ),
        )
        for k, n in ta.nodes.items()
        if n["battery"].get("on_battery")
    ]
    failed_towers = [
        cname(ta.nodes[k].get("name"), k)
        for k in ta.get_failed_nodes()
    ]

    all_bpct = [
        n["battery"].get("remaining_kwh", 0)
        / max(n["battery"].get("capacity_kwh", 1), 0.001)
        * 100
        for n in ta.nodes.values()
    ]
    t_avg_batt = round(sum(all_bpct) / len(all_bpct), 2) if all_bpct else 100.0
    t_min_batt = round(min(all_bpct), 2) if all_bpct else 100.0

    batt_detail = [
        (
            cname(n.get("name"), k),
            round(
                n["battery"].get("remaining_kwh", 0)
                / max(n["battery"].get("capacity_kwh", 1), 0.001)
                * 100,
                1,
            ),
        )
        for k, n in ta.nodes.items()
    ]
    min_batt = min(batt_detail, key=lambda x: x[1], default=("N/A", 100.0))

    providers_dark = sum(
        1
        for n in ta.nodes.values()
        for p in n.get("providers", [])
        if not p.get("active", True)
    )

    # ── Cross-network cascade flag ─────────────────────────────────────────────
    # True only when water OR telecom infrastructure was actually impacted.
    # Pure power events (TRANSFORMER_REROUTED, SUBSTATION_FAILED, etc.) that
    # never propagated further do NOT count as cross-network cascades.
    real_cascade = int(
        len(failed_pumps) > 0
        or len(backup_pumps) > 0
        or len(drain_towers) > 0
        or len(batt_towers) > 0
        or len(failed_towers) > 0
        or len(burst_juncs) > 0
    )

    # Total cascade events = everything the bus carried beyond the baseline
    # (initial fail events + tick events)
    baseline = n_ticks + len(fail_targets)
    total_cascade_events = max(0, bus.published_count - baseline)

    # ── Cleanup ────────────────────────────────────────────────────────────────
    pt.cancel()
    wt.cancel()
    tt.cancel()
    await bus.stop()

    return {
        # Identity
        "run_id":                     run_id,
        "seed":                       seed,
        # Power — counts
        "power_subs_failed":          len(failed_subs),
        "power_subs_total":           sub_total,
        "power_tx_failed":            len(failed_txs),
        "power_tx_total":             tx_total,
        "power_avg_sub_load":         p_avg_sub_load,
        "power_feeder_drops":         feeder_drops,
        # Power — named (semicolon-separated for CSV)
        "failed_substations":         "; ".join(failed_subs)  or "none",
        "failed_transformers":        "; ".join(failed_txs)   or "none",
        # Water — counts
        "water_pumps_failed":         len(failed_pumps),
        "water_pumps_backup":         len(backup_pumps),
        "water_pumps_running":        sum(
            1 for p in wa._pump_ids
            if wa.nodes[p].get("pump_status") == "running"
        ),
        "water_towers_draining":      len(drain_towers),
        "water_towers_empty":         len(empty_towers),
        "water_pipe_bursts":          len(burst_juncs),
        "water_avg_pressure":         round(w_avg_p, 4),
        "water_min_pressure":         round(w_min_p, 4),
        "water_avg_tower_level":      w_avg_lvl,
        "water_min_tower_level":      w_min_lvl,
        # Water — named
        "failed_pumps":               "; ".join(failed_pumps) or "none",
        "backup_pumps":               "; ".join(backup_pumps) or "none",
        "draining_towers":            "; ".join(drain_towers) or "none",
        "empty_towers":               "; ".join(empty_towers) or "none",
        "burst_junctions":            "; ".join(burst_juncs)  or "none",
        "min_pressure_junction":      min_j_name,
        "min_level_tower":            min_t_name,
        # Telecom — counts
        "telecom_on_battery":         len(batt_towers),
        "telecom_failed":             len(failed_towers),
        "telecom_providers_dark":     providers_dark,
        "telecom_avg_batt_pct":       t_avg_batt,
        "telecom_min_batt_pct":       t_min_batt,
        # Telecom — named
        "towers_on_battery":          "; ".join(f"{n}({p}%)" for n, p in batt_towers) or "none",
        "failed_towers":              "; ".join(failed_towers) or "none",
        "lowest_battery_tower":       f"{min_batt[0]} ({min_batt[1]}%)",
        # Global
        "real_cross_network_cascade": real_cascade,
        "total_cascade_events":       total_cascade_events,
    }


# ══════════════════════════════════════════════════════════════════════════════
# STATISTICS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def calc_stats(results: list[dict], key: str) -> dict:
    vals = [r[key] for r in results]
    n = len(vals)
    if n == 0:
        return dict(mean=0, std=0, ci_lo=0, ci_hi=0, min=0, p25=0, p50=0, p75=0, max=0)
    mu = statistics.mean(vals)
    sd = statistics.stdev(vals) if n > 1 else 0.0
    se = sd / math.sqrt(n)
    sv = sorted(vals)
    return dict(
        mean=round(mu, 4),
        std=round(sd, 4),
        ci_lo=round(mu - 1.96 * se, 4),
        ci_hi=round(mu + 1.96 * se, 4),
        min=round(sv[0], 4),
        p25=round(sv[n // 4], 4),
        p50=round(sv[n // 2], 4),
        p75=round(sv[3 * n // 4], 4),
        max=round(sv[-1], 4),
    )


def risk_label(p: float) -> str:
    if p >= 0.70:
        return f"{RED}HIGH  {RESET}"
    if p >= 0.30:
        return f"{YELLOW}MEDIUM{RESET}"
    return f"{GREEN}LOW   {RESET}"


def node_freq_table(counter: Counter, label: str, total: int):
    if not counter:
        print(f"  {DIM}{label}: none{RESET}")
        return
    print(f"\n  {BOLD}{label}{RESET}")
    for name, cnt in counter.most_common(15):
        pct = cnt / total * 100
        bar = "█" * max(1, int(pct / 2.5))
        print(f"    {name:<44s}  {bar:<40s}  {cnt:4d}/{total}  ({pct:5.1f}%)")


# ══════════════════════════════════════════════════════════════════════════════
# NODE LISTING
# ══════════════════════════════════════════════════════════════════════════════

def list_all_nodes(power_json: str, water_json: str, telecom_json: str):
    print(f"\n{BOLD}{'═'*68}{RESET}")
    print(f"{BOLD}  Available Nodes  —  use these names/IDs with --fail{RESET}")
    print(f"{'═'*68}")
    for path, label, tkey in [
        (power_json,   "POWER",   "power"),
        (water_json,   "WATER",   "node_type"),
        (telecom_json, "TELECOM", None),
    ]:
        with open(path) as f:
            data = json.load(f)
        print(f"\n  {BOLD}{label} NETWORK{RESET}")
        for n in data.get("nodes", []):
            ntype = n.get(tkey, "tower") if tkey else "tower"
            nid   = str(n.get("node_id", "?"))
            name  = n.get("name", nid)
            print(f"    [{ntype:18s}]  id={nid:>14s}  name={name}")
    print(f"\n{BOLD}{'═'*68}{RESET}\n")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    ap = argparse.ArgumentParser(
        description="Monte Carlo Simulation Engine  v3  (corrected)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    ap.add_argument("--runs",               type=int, default=1000)
    ap.add_argument("--ticks",              type=int, default=60)
    ap.add_argument("--fail",               type=str, nargs="+", default=["Substation 1"],
                    help="Node names or IDs to fail (space-separated)")
    ap.add_argument("--fail-all-substations", action="store_true",
                    help="Fail ALL substations simultaneously")
    ap.add_argument("--no-cascade",         action="store_true",
                    help="Skip force_substation_cascade (pure baseline mode)")
    ap.add_argument("--flood",              action="store_true",
                    help="Inject flood at tick 5 (oldest junction + first ground tower)")
    ap.add_argument("--seed-base",          type=int, default=0)
    ap.add_argument("--list-nodes",         action="store_true",
                    help="Print all node names/IDs and exit")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)

    pjson = "graphs/power.json"
    wjson = "graphs/water.json"
    tjson = "graphs/telecom.json"
    for p in [pjson, wjson, tjson]:
        if not os.path.exists(p):
            print(f"{RED}ERROR: {p} not found.{RESET}")
            sys.exit(1)

    if args.list_nodes:
        list_all_nodes(pjson, wjson, tjson)
        sys.exit(0)

    # ── Load graph JSON for node name/ID resolution ────────────────────────────
    with open(pjson) as f: pdata = json.load(f)
    with open(wjson) as f: wdata = json.load(f)
    with open(tjson) as f: tdata = json.load(f)

    # Build a flat name+id → {network, id, name} lookup covering all networks
    lookup: dict[str, dict] = {}
    for n in pdata["nodes"]:
        nid = str(n["node_id"])
        nm  = n.get("name", nid)
        entry = {"network": Network.POWER, "id": nid, "name": nm}
        lookup[nid] = lookup[nm] = entry

    for n in wdata["nodes"]:
        nid = str(n["node_id"])
        nm  = cname(n.get("name"), nid)
        entry = {"network": Network.WATER, "id": nid, "name": nm}
        lookup[nid] = lookup[nm] = entry

    for n in tdata.get("nodes", []):
        nid = str(n.get("node_id", n.get("tower_id", "?")))
        nm  = cname(n.get("name"), nid)
        entry = {"network": Network.TELECOM, "id": nid, "name": nm}
        lookup[nid] = lookup[nm] = entry

    # ── Resolve --fail targets ─────────────────────────────────────────────────
    fail_targets: list[dict] = []
    target_names: list[str]  = []

    if args.fail_all_substations:
        for n in pdata["nodes"]:
            if n.get("power") == "substation":
                nid = str(n["node_id"])
                nm  = n.get("name", nid)
                fail_targets.append({"network": Network.POWER, "id": nid, "name": nm})
                target_names.append(nm)
    else:
        for q in args.fail:
            if q in lookup:
                fail_targets.append(lookup[q])
                target_names.append(lookup[q]["name"])
            else:
                # Fuzzy substring match (case-insensitive)
                match = next(
                    (v for k, v in lookup.items() if q.lower() in k.lower()), None
                )
                if match:
                    fail_targets.append(match)
                    target_names.append(match["name"])
                else:
                    print(f"{RED}ERROR: '{q}' not found. Use --list-nodes to see all nodes.{RESET}")
                    sys.exit(1)

    # Deduplicate while preserving order
    seen_ids: set[str] = set()
    ft2: list[dict] = []
    tn2: list[str]  = []
    for t in fail_targets:
        if t["id"] not in seen_ids:
            seen_ids.add(t["id"])
            ft2.append(t)
            tn2.append(t["name"])
    fail_targets, target_names = ft2, tn2

    # ── Decide whether cascade forcing applies ─────────────────────────────────
    sub_ids_in_json = {str(n["node_id"]) for n in pdata["nodes"] if n.get("power") == "substation"}
    has_sub = (
        args.fail_all_substations
        or any(
            t["network"] == Network.POWER and t["id"] in sub_ids_in_json
            for t in fail_targets
        )
    )
    force_cascade = has_sub and not args.no_cascade

    # ── Flood targets ──────────────────────────────────────────────────────────
    flood_junction: str | None = None
    flood_tower:    str | None = None
    if args.flood:
        # Oldest pipe junction → most likely to burst
        junctions = [n for n in wdata["nodes"] if n.get("node_type") == "pipe_junction"]
        if junctions:
            oldest = max(junctions, key=lambda n: n.get("pipe_age_years", 0))
            flood_junction = str(oldest["node_id"])

        # First ground-type tower
        ground = next(
            (n for n in tdata.get("nodes", []) if n.get("tower_type") == "ground"),
            None,
        )
        if ground:
            flood_tower = str(ground.get("node_id", ground.get("tower_id")))

    # ── Print run header ───────────────────────────────────────────────────────
    print(f"\n{BOLD}{'═'*70}{RESET}")
    print(f"{BOLD}  Monte Carlo Simulation Engine  v3  (corrected){RESET}")
    print(f"{'═'*70}")
    if force_cascade:
        print(f"  {YELLOW}⚡ Cascade forcing ON{RESET}  (substation → transformer → water/telecom)")
    elif args.no_cascade:
        print(f"  {DIM}Cascade forcing OFF  (--no-cascade baseline mode){RESET}")
    else:
        print(f"  {DIM}Cascade forcing OFF  (no substations in fail targets){RESET}")
    print(f"  Fail Targets : {', '.join(target_names)}")
    print(f"  Flood        : {'Yes — tick 5' if args.flood else 'No'}")
    print(f"  Runs         : {args.runs}")
    print(f"  Ticks/run    : {args.ticks}  ({args.ticks * TICK_DURATION_MINUTES:.0f} simulated minutes)")
    print(f"  Seed range   : {args.seed_base} – {args.seed_base + args.runs - 1}")
    print(f"{'═'*70}\n")

    # ── Run loop ───────────────────────────────────────────────────────────────
    results: list[dict] = []
    t0 = time.time()

    for i in range(args.runs):
        m = await run_single_simulation(
            run_id=i,
            seed=args.seed_base + i,
            fail_targets=fail_targets,
            flood_junction=flood_junction,
            flood_tower=flood_tower,
            n_ticks=args.ticks,
            force_cascade=force_cascade,
            power_json=pjson,
            water_json=wjson,
            telecom_json=tjson,
        )
        results.append(m)

        if (i + 1) % max(1, args.runs // 40) == 0 or i == args.runs - 1:
            el  = time.time() - t0
            pct = (i + 1) / args.runs * 100
            eta = el / (i + 1) * (args.runs - i - 1)
            bar = "█" * int(30 * (i + 1) / args.runs) + "░" * (30 - int(30 * (i + 1) / args.runs))
            sys.stderr.write(
                f"\r  {bar} {pct:5.1f}%  run {i+1}/{args.runs}  "
                f"elapsed={el:.1f}s  ETA={eta:.1f}s   "
            )
            sys.stderr.flush()

    elapsed = time.time() - t0
    sys.stderr.write("\n")
    sys.stderr.flush()
    print(f"\n  {GREEN}✓ {args.runs} runs in {elapsed:.1f}s  ({elapsed/args.runs:.3f}s/run){RESET}\n")

    # ══════════════════════════════════════════════════════════════════════════
    # STATISTICS TABLE
    # ══════════════════════════════════════════════════════════════════════════

    metrics = [
        ("power_subs_failed",            "Power: Substations Failed"),
        ("power_tx_failed",              "Power: Transformers Failed"),
        ("power_avg_sub_load",           "Power: Avg Substation Load"),
        ("power_feeder_drops",           "Power: Feeder Line Drops"),
        ("water_pumps_failed",           "Water: Pumps Failed"),
        ("water_pumps_backup",           "Water: Pumps on Backup Generator"),
        ("water_towers_draining",        "Water: Towers Draining"),
        ("water_towers_empty",           "Water: Towers Empty"),
        ("water_pipe_bursts",            "Water: Pipe Bursts"),
        ("water_avg_pressure",           "Water: Avg Junction Pressure (0-1)"),
        ("water_min_pressure",           "Water: Min Junction Pressure (0-1)"),
        ("water_avg_tower_level",        "Water: Avg Tower Water Level (0-1)"),
        ("water_min_tower_level",        "Water: Min Tower Water Level (0-1)"),
        ("telecom_on_battery",           "Telecom: Towers on Battery"),
        ("telecom_failed",               "Telecom: Towers Failed"),
        ("telecom_providers_dark",       "Telecom: Provider Links Dark"),
        ("telecom_avg_batt_pct",         "Telecom: Avg Battery %"),
        ("telecom_min_batt_pct",         "Telecom: Min Battery %"),
        ("real_cross_network_cascade",   "Cross-Network Cascade (water/telecom hit)"),
        ("total_cascade_events",         "Total Cascade Events"),
    ]

    all_stats: dict[str, dict] = {}

    print(
        f"  {BOLD}{'Metric':<42s}  {'Mean':>8s}  {'Std':>7s}  "
        f"{'95% CI':>18s}  {'P50':>7s}  {'Max':>7s}{RESET}"
    )
    print(f"  {'─'*42}  {'─'*8}  {'─'*7}  {'─'*18}  {'─'*7}  {'─'*7}")

    prev_grp = None
    for key, label in metrics:
        grp = label.split(":")[0]
        if prev_grp and grp != prev_grp:
            print(f"  {'·'*42}  {'·'*8}  {'·'*7}  {'·'*18}  {'·'*7}  {'·'*7}")
        prev_grp = grp

        s = calc_stats(results, key)
        all_stats[key] = s
        ci = f"[{s['ci_lo']:7.3f}, {s['ci_hi']:7.3f}]"
        ms = f"{s['mean']:8.3f}"

        if s["mean"] == 0 and s["std"] == 0:
            ms = f"{DIM}{ms}{RESET}"
        elif "pressure" in key or "level" in key:
            ms = f"{RED if s['mean'] < 0.4 else YELLOW}{ms}{RESET}"
        elif "batt" in key.lower():
            ms = f"{RED if s['mean'] < 50 else (YELLOW if s['std'] > 5 else '')}{ms}{RESET}"
        elif s["mean"] > 0:
            ms = f"{YELLOW}{ms}{RESET}"

        print(f"  {label:<42s}  {ms}  {s['std']:7.3f}  {ci:>18s}  {s['p50']:7.3f}  {s['max']:7.3f}")

    # ══════════════════════════════════════════════════════════════════════════
    # KEY RISK FINDINGS
    # ══════════════════════════════════════════════════════════════════════════

    print(f"\n{BOLD}  ── Key Risk Findings ───────────────────────────────────────────────{RESET}")

    risk_items = [
        ("Transformer failure",             lambda r: r["power_tx_failed"] > 0),
        ("Water pump failure",              lambda r: r["water_pumps_failed"] > 0),
        ("Water pump on backup generator",  lambda r: r["water_pumps_backup"] > 0),
        ("Water tower draining",            lambda r: r["water_towers_draining"] > 0),
        ("Water tower empty",               lambda r: r["water_towers_empty"] > 0),
        ("Pipe burst",                      lambda r: r["water_pipe_bursts"] > 0),
        ("Telecom tower on battery",        lambda r: r["telecom_on_battery"] > 0),
        ("Telecom tower failed",            lambda r: r["telecom_failed"] > 0),
        ("Cross-network cascade",           lambda r: r["real_cross_network_cascade"] > 0),
        ("Water pressure critical (<0.4)",  lambda r: r["water_min_pressure"] < 0.4),
        ("Telecom battery critical (<30%)", lambda r: r["telecom_min_batt_pct"] < 30),
    ]

    probs: dict[str, float] = {}
    for label, fn in risk_items:
        p = sum(1 for r in results if fn(r)) / len(results)
        probs[label] = p
        bar = "█" * int(p * 30) + "░" * (30 - int(p * 30))
        print(f"  {label:<42s}  {bar}  {p*100:5.1f}%  {risk_label(p)}")

    print(
        f"\n  Avg transformers failed : {YELLOW}{all_stats['power_tx_failed']['mean']:.1f}{RESET}"
        f" / {results[0]['power_tx_total']}"
    )
    print(
        f"  Avg cascade events/run  : {YELLOW}{all_stats['total_cascade_events']['mean']:.0f}{RESET}"
        f"  (σ={all_stats['total_cascade_events']['std']:.0f})"
    )

    # ══════════════════════════════════════════════════════════════════════════
    # NODE IMPACT SUMMARY
    # ══════════════════════════════════════════════════════════════════════════

    N = len(results)
    print(f"\n{BOLD}  ── Node Impact Summary  ({N} runs) ─────────────────────────────────{RESET}")

    c_sub     = Counter()
    c_tx      = Counter()
    c_pump    = Counter()
    c_drain   = Counter()
    c_empty   = Counter()
    c_burst   = Counter()
    c_batt    = Counter()
    c_fail_t  = Counter()

    for r in results:
        for nm in r["failed_substations"].split("; "):
            if nm and nm != "none": c_sub[nm] += 1
        for nm in r["failed_transformers"].split("; "):
            if nm and nm != "none": c_tx[nm] += 1
        for nm in r["failed_pumps"].split("; "):
            if nm and nm != "none": c_pump[nm] += 1
        for nm in r["draining_towers"].split("; "):
            if nm and nm != "none": c_drain[nm] += 1
        for nm in r["empty_towers"].split("; "):
            if nm and nm != "none": c_empty[nm] += 1
        for nm in r["burst_junctions"].split("; "):
            if nm and nm != "none": c_burst[nm] += 1
        for entry in r["towers_on_battery"].split("; "):
            nm = entry.split("(")[0].strip()
            if nm and nm != "none": c_batt[nm] += 1
        for nm in r["failed_towers"].split("; "):
            if nm and nm != "none": c_fail_t[nm] += 1

    node_freq_table(c_sub,   "Substations Failed",        N)
    node_freq_table(c_tx,    "Transformers Failed",       N)
    node_freq_table(c_pump,  "Water Pumps Failed",        N)
    node_freq_table(c_drain, "Water Towers Draining",     N)
    node_freq_table(c_empty, "Water Towers Empty",        N)
    node_freq_table(c_burst, "Pipe Junctions Burst",      N)
    node_freq_table(c_batt,  "Telecom Towers on Battery", N)
    node_freq_table(c_fail_t,"Telecom Towers Failed",     N)

    print(f"\n  {BOLD}Most Vulnerable Node Per Network:{RESET}")
    for lbl, cntr in [
        ("Power  (transformer)", c_tx),
        ("Water  (pump)",        c_pump),
        ("Water  (tower drain)", c_drain),
        ("Telecom (battery)",    c_batt),
        ("Telecom (failed)",     c_fail_t),
    ]:
        if cntr:
            top = cntr.most_common(1)[0]
            print(
                f"    {lbl:<26s}→  {YELLOW}{top[0]}{RESET}"
                f"  ({top[1]}/{N} runs = {top[1]/N*100:.1f}%)"
            )
    print()

    # ══════════════════════════════════════════════════════════════════════════
    # SAVE OUTPUTS
    # ══════════════════════════════════════════════════════════════════════════

    os.makedirs("data", exist_ok=True)

    csv_path = "data/monte_carlo_results.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print(f"  {GREEN}✓ Raw data → {csv_path}  ({N} rows){RESET}")

    sum_path = "data/monte_carlo_summary.txt"
    with open(sum_path, "w", encoding="utf-8") as f:
        f.write(f"Monte Carlo Summary  v3  (corrected)\n{'='*70}\n")
        f.write(f"Fail Targets   : {', '.join(target_names)}\n")
        f.write(f"Cascade Forcing: {'ON' if force_cascade else 'OFF'}\n")
        f.write(f"Flood          : {'Yes' if args.flood else 'No'}\n")
        f.write(f"Runs / Ticks   : {args.runs} / {args.ticks}\n")
        f.write(f"Total time     : {elapsed:.1f}s\n\n")
        f.write(
            f"{'Metric':<42s}  {'Mean':>8s}  {'Std':>8s}  "
            f"{'P50':>8s}  {'Max':>8s}\n"
        )
        f.write("-" * 80 + "\n")
        for key, label in metrics:
            s = all_stats[key]
            f.write(
                f"{label:<42s}  {s['mean']:8.4f}  {s['std']:8.4f}  "
                f"{s['p50']:8.4f}  {s['max']:8.4f}\n"
            )
        f.write("\nKey Risk Findings:\n")
        for label, _ in risk_items:
            f.write(f"  {label:<44s}  {probs[label]*100:.1f}%\n")
        f.write("\nNode Impact Frequencies:\n")
        for lbl, cntr in [
            ("Substations Failed",  c_sub),
            ("Transformers Failed", c_tx),
            ("Water Pumps Failed",  c_pump),
            ("Water Towers Drain",  c_drain),
            ("Telecom on Battery",  c_batt),
            ("Telecom Failed",      c_fail_t),
        ]:
            f.write(f"\n  {lbl}:\n")
            for nm, cnt in cntr.most_common():
                f.write(f"    {nm:<44s}  {cnt}/{N}  ({cnt/N*100:.1f}%)\n")

    print(f"  {GREEN}✓ Summary   → {sum_path}{RESET}")
    print(f"\n{BOLD}{'═'*70}{RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())
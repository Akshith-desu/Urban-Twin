"""
node_physics.py  —  Phase 3 prerequisite
=========================================
Single authoritative source for:
  1. Every field every node type must carry (initialised to healthy defaults)
  2. Every physics formula used in cascade propagation
  3. The PendingCascade descriptor stored on each node so agents can pull
     "what will happen next to me?" without scanning the full event log

Import this everywhere.  Never hardcode a threshold or formula in an agent.

Networks: POWER · WATER · ROAD · TELECOM
Sub-types per network are listed under each section.

USAGE
-----
  from node_physics import (
      init_power_node, init_water_node, init_road_node, init_telecom_node,
      PowerPhysics, WaterPhysics, RoadPhysics, TelecomPhysics,
      describe_pending_cascade,
  )
"""

import math
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from enum import Enum


# ═══════════════════════════════════════════════════════════════════════════════
# 0.  SHARED ENUMS AND CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

class NodeHealth(float, Enum):
    """Named thresholds for health float (0.0 – 1.0)."""
    HEALTHY   = 1.0
    DEGRADED  = 0.5   # health ≤ 0.5  → emit NODE_DEGRADED
    CRITICAL  = 0.3   # health ≤ 0.3  → one more bad draw → NODE_FAILED
    FAILED    = 0.0   # health = 0    → node is out of service


class TimeOfDay(str, Enum):
    RUSH_HOUR = "rush_hour"   # 08:00–10:00, 17:00–20:00
    DAYTIME   = "daytime"     # 10:00–17:00
    NIGHT     = "night"       # 20:00–08:00


# Simulation tick = 1 real minute (set in simulation.py)
TICK_MINUTES: float = 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  PENDING CASCADE DESCRIPTOR
#     Stored directly on the node dict under key "pending_cascades".
#     Agents read this list each tick to know what is queued for that node
#     without scanning the full event log.
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PendingCascade:
    """
    One pending cascade event queued against a node.

    Fields
    ------
    source_network   : which network triggered this cascade
    source_node      : the node ID that caused it
    event_type       : the EventType string that will be emitted when it fires
    fire_at_tick     : absolute simulation tick when this fires
    failure_prob     : probability drawn at scheduling time (already passed)
    cascade_depth    : depth counter from the originating event
    description      : human-readable sentence for the agent LLM prompt
                       (also shown in the dashboard feed)
    metadata         : any extra k/v pairs the target agent needs
    """
    source_network : str
    source_node    : str
    event_type     : str
    fire_at_tick   : int
    failure_prob   : float
    cascade_depth  : int
    description    : str
    metadata       : Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def describe_pending_cascade(
    source_network: str,
    source_node: str,
    target_network: str,
    target_node: str,
    event_type: str,
    fire_at_tick: int,
    current_tick: int,
    dep_type: str,
    failure_prob: float,
    cascade_depth: int,
    extra: Optional[Dict[str, Any]] = None,
) -> PendingCascade:
    """
    Build a PendingCascade with a plain-English description sentence.
    Call this whenever a cascade is scheduled; attach to target node's
    pending_cascades list.

    Example description produced:
      "Power loss at PS-CMH (prob=0.95) will cause PRESSURE_DROP on
       water pump WP-MAIN in 2 ticks (power_supply dependency)."
    """
    ticks_away = fire_at_tick - current_tick
    desc = (
        f"{source_network.title()} failure at {source_node} "
        f"(p={failure_prob:.2f}) will cause {event_type} "
        f"on {target_network} node {target_node} "
        f"in {ticks_away} tick(s) [{dep_type} dependency]."
    )
    return PendingCascade(
        source_network=source_network,
        source_node=source_node,
        event_type=event_type,
        fire_at_tick=fire_at_tick,
        failure_prob=failure_prob,
        cascade_depth=cascade_depth,
        description=desc,
        metadata=extra or {},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  POWER NETWORK
# ═══════════════════════════════════════════════════════════════════════════════

# ── 2a. Physics constants ─────────────────────────────────────────────────────

class PowerPhysics:
    """
    All constants and formulas for the power agent.
    Sources: IEEE 9-bus model, BESCOM operational norms, Phase 3 TDD §5.1.

    LOAD REDISTRIBUTION
    -------------------
    After a substation fails, its load (failed_load) is split among survivors
    proportional to their remaining capacity headroom.

        extra_share_i = failed_load × (headroom_i / total_headroom)
        new_load_i    = current_load_i + extra_share_i
        where headroom_i = max_load_i − current_load_i  (clamped ≥ 0)

    OVERLOAD HEALTH DEGRADATION  (Rule P2, TDD §5.1)
    -------------------------------------------------
        health = max(HEALTH_FLOOR, 1.0 − (load − 1.0) × OVERLOAD_HEALTH_SLOPE)

        OVERLOAD_HEALTH_SLOPE = 1.0   →  a substation at 140% load → health = 0.6
        HEALTH_FLOOR          = 0.2   →  never let health go below 0.2 from overload
                                          alone (it must fail via probability draw)

    OVERLOAD FAILURE DRAW
    ---------------------
    Each tick a substation is above OVERLOAD_THRESHOLD:
        p_fail = min(1.0, OVERLOAD_FAILURE_BASE_PROB × (load − OVERLOAD_THRESHOLD))
    Random draw: if random() < p_fail → NODE_FAILED

    TIME-OF-DAY LOAD MULTIPLIER  (Rule P3)
    ---------------------------------------
        rush_hour → base_load × 1.4
        daytime   → base_load × 1.1
        night     → base_load × 0.6
    """

    # ── thresholds ──────────────────────────────────────────────────────────
    OVERLOAD_THRESHOLD       : float = 1.0    # above this → degradation starts
    CRITICAL_LOAD_THRESHOLD  : float = 1.35   # above this → likely fail next tick
    HEALTH_FLOOR             : float = 0.2    # overload alone cannot go below this
    OVERLOAD_HEALTH_SLOPE    : float = 1.0    # 1% over cap → 1% health drop
    OVERLOAD_FAILURE_BASE_PROB: float = 0.15  # per-tick fail prob base when overloaded

    # ── load redistribution ─────────────────────────────────────────────────
    MIN_HEADROOM             : float = 0.01   # avoid div/0 when all subs are full

    # ── time-of-day multipliers ─────────────────────────────────────────────
    TOD_MULTIPLIER : Dict[str, float] = {
        TimeOfDay.RUSH_HOUR: 1.4,
        TimeOfDay.DAYTIME:   1.1,
        TimeOfDay.NIGHT:     0.6,
    }

    # ── SCADA vulnerability bump (from telecom loss, Rule T2) ───────────────
    SCADA_VULN_BUMP          : float = 0.30   # added to substation vulnerability
    MAX_VULNERABILITY        : float = 1.0

    # ── node type → base criticality (matches graph_builder.py) ─────────────
    BASE_CRITICALITY : Dict[str, float] = {
        "plant":       10.0,
        "substation":   8.0,
        "transformer":  7.0,
        "generator":    7.0,
        "data_center":  6.0,
        "fuel":         5.0,
        "default":      5.0,
    }

    @staticmethod
    def overload_health(load: float) -> float:
        """Continuous health given a load value.  Returns 1.0 if load ≤ 1.0."""
        if load <= PowerPhysics.OVERLOAD_THRESHOLD:
            return 1.0
        raw = 1.0 - (load - PowerPhysics.OVERLOAD_THRESHOLD) * PowerPhysics.OVERLOAD_HEALTH_SLOPE
        return max(PowerPhysics.HEALTH_FLOOR, raw)

    @staticmethod
    def overload_fail_prob(load: float) -> float:
        """Per-tick probability of outright failure when overloaded."""
        if load <= PowerPhysics.OVERLOAD_THRESHOLD:
            return 0.0
        return min(1.0, PowerPhysics.OVERLOAD_FAILURE_BASE_PROB * (load - PowerPhysics.OVERLOAD_THRESHOLD))

    @staticmethod
    def apply_tod_multiplier(base_load: float, tod: str) -> float:
        mult = PowerPhysics.TOD_MULTIPLIER.get(tod, 1.0)
        return min(base_load * mult, 2.0)  # cap at 2× to avoid nonsense

    @staticmethod
    def redistribute_load(
        failed_load: float,
        survivors: List[Dict[str, float]],   # list of {current_load, max_load}
    ) -> List[float]:
        """
        Return new load values for each survivor after absorbing failed_load.
        Proportional to remaining headroom.
        """
        headrooms = [max(PowerPhysics.MIN_HEADROOM, s["max_load"] - s["current_load"])
                     for s in survivors]
        total_headroom = sum(headrooms)
        new_loads = []
        for s, hw in zip(survivors, headrooms):
            extra = failed_load * (hw / total_headroom)
            new_loads.append(s["current_load"] + extra)
        return new_loads


# ── 2b. Node field initialiser ────────────────────────────────────────────────

def init_power_node(
    node_id: str,
    node_type: str = "substation",
    base_load: float = 0.65,
    max_load: float = 1.0,
    voltage_kv: float = 11.0,
    operator: str = "BESCOM",
    name: str = "",
    is_manual: bool = False,
) -> Dict[str, Any]:
    """
    Return the complete attribute dict for a power node.
    Merge this over the existing node dict in Phase 1 graphs.

    Fields
    ------
    health              float   1.0 = fully healthy, 0.0 = failed
    load                float   current load as fraction of max_load
    max_load            float   rated capacity (1.0 = 100% of rated)
    base_load           float   load before time-of-day multiplier
    voltage_kv          float   operating voltage in kV
    voltage_deviation   float   deviation from nominal (0 = nominal)
    overload_count      int     consecutive ticks above OVERLOAD_THRESHOLD
    vulnerability       float   0.0–1.0, raised by SCADA loss (Rule T2)
    is_island           bool    True if disconnected from all healthy substations
    has_backup_gen      bool    True if on-site diesel generator exists
    backup_gen_runtime  float   hours of diesel backup remaining (0 if none)
    scada_linked        bool    True if telecom SCADA monitoring is active
    node_type           str     substation / transformer / generator / plant / etc.
    operator            str     e.g. BESCOM
    name                str     human-readable label for LLM reports
    criticality         float   weight used in orchestrator WFR
    health_history      list    last 5 health values (for trend detection)
    pending_cascades    list    list of PendingCascade.to_dict() dicts
    cascade_description str     plain-English summary of worst pending cascade
                                (pulled by agent for LLM prompt)
    """
    crit = PowerPhysics.BASE_CRITICALITY.get(node_type, PowerPhysics.BASE_CRITICALITY["default"])
    return {
        # ── identity ──────────────────────────────────────────────────────
        "network":           "power",
        "node_id":           node_id,
        "node_type":         node_type,
        "name":              name,
        "operator":          operator,
        # ── health state ─────────────────────────────────────────────────
        "health":            1.0,
        "health_history":    [1.0, 1.0, 1.0, 1.0, 1.0],
        "vulnerability":     0.0,
        "is_island":         False,
        # ── electrical ───────────────────────────────────────────────────
        "load":              base_load,
        "base_load":         base_load,
        "max_load":          max_load,
        "voltage_kv":        voltage_kv,
        "voltage_deviation": 0.0,      # fraction of nominal (positive = over-voltage)
        "overload_count":    0,        # consecutive ticks above threshold
        "frequency_hz":      50.0,     # Indian grid nominal
        "power_factor":      0.9,
        # ── backup ───────────────────────────────────────────────────────
        "has_backup_gen":    is_manual,    # manual/known substations assumed to have backup
        "backup_gen_runtime": 4.0 if is_manual else 0.0,   # hours
        # ── telecom dependency ───────────────────────────────────────────
        "scada_linked":      True,
        "scada_coverage_ok": True,
        # ── criticality ──────────────────────────────────────────────────
        "criticality":       crit,
        # ── cascade bookkeeping ──────────────────────────────────────────
        "pending_cascades":      [],    # list of PendingCascade.to_dict()
        "cascade_description":   "",    # worst pending cascade in plain English
        "flood_risk":            False,
        "pop_density":           1.0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  WATER NETWORK
# ═══════════════════════════════════════════════════════════════════════════════

class WaterPhysics:
    """
    All constants and formulas for the water agent.
    Sources: BWSSB operational norms, hydraulic engineering (Bernoulli + pipe
    friction), Phase 3 TDD §5.2.

    PRESSURE DECAY  (Rule W1)
    -------------------------
    When a pump loses power, pressure at the pump decays exponentially:

        new_pressure = current_pressure × failure_prob
                       × exp(−DECAY_CONSTANT × elapsed_minutes)

        DECAY_CONSTANT = 0.085 min⁻¹
            → pressure halves in ≈ 8.2 minutes  (ln2 / 0.085)
            → calibrated to 150mm BWSSB distribution mains

    PIPE BFS PRESSURE ATTENUATION  (Rule W2)
    -----------------------------------------
    As pressure loss propagates through the pipe graph:

        neighbour_pressure = upstream_pressure
                             × (1 − (pipe_length / MAX_PIPE_LENGTH) × BFS_ATTENUATION)

        MAX_PIPE_LENGTH = 500 m   (longest single pipe segment assumed)
        BFS_ATTENUATION = 0.30    (30% additional loss per 500 m segment)

    CONTAMINATION DILUTION  (flood → water, dependency 'flood_contamination')
    --------------------------------------------------------------------------
        new_contamination = current_contamination
                            + FLOOD_CONTAMINATION_RATE × flood_severity
                            × (1 − current_contamination)   # logistic growth

        FLOOD_CONTAMINATION_RATE = 0.25 per tick (25% of remaining clean capacity)
        Safe threshold: contamination ≤ 0.10  (>10% → potable water loss)

    FLOW RATE  (derived from pressure)
    ------------------------------------
        flow_rate = base_flow × sqrt(max(0, pressure))
        (Torricelli/Bernoulli approximation for pipe flow)
    """

    # ── thresholds ──────────────────────────────────────────────────────────
    LOW_PRESSURE_THRESHOLD    : float = 0.50   # below → emit PRESSURE_DROP
    CRITICAL_PRESSURE         : float = 0.30   # below → BFS propagation starts
    FAILED_PRESSURE           : float = 0.05   # effectively zero
    CONTAMINATION_SAFE        : float = 0.10   # above → potable water lost
    CONTAMINATION_CRITICAL    : float = 0.50   # above → health hazard

    # ── pressure decay ──────────────────────────────────────────────────────
    DECAY_CONSTANT            : float = 0.085  # min⁻¹  (see formula above)

    # ── pipe BFS attenuation ────────────────────────────────────────────────
    MAX_PIPE_LENGTH           : float = 500.0  # metres, longest segment
    BFS_ATTENUATION           : float = 0.30   # fraction per MAX_PIPE_LENGTH

    # ── flood contamination ─────────────────────────────────────────────────
    FLOOD_CONTAMINATION_RATE  : float = 0.25   # per tick, logistic

    # ── flow ────────────────────────────────────────────────────────────────
    BASE_FLOW_PUMP            : float = 1.0
    BASE_FLOW_JUNCTION        : float = 0.5

    # ── criticality ─────────────────────────────────────────────────────────
    BASE_CRITICALITY : Dict[str, float] = {
        "pump_station":  9.0,
        "pipe_junction": 2.0,
        "reservoir":     9.0,
        "storage_tank":  7.0,
        "valve":         4.0,
    }

    @staticmethod
    def decay_pressure(current_pressure: float,
                       elapsed_minutes: float,
                       failure_prob: float = 0.95) -> float:
        """
        Pressure at pump station elapsed_minutes after power loss.
        failure_prob comes from dependency_edges.json.
        """
        return current_pressure * failure_prob * math.exp(
            -WaterPhysics.DECAY_CONSTANT * elapsed_minutes
        )

    @staticmethod
    def propagate_pressure(upstream_pressure: float, pipe_length_m: float) -> float:
        """
        Downstream pressure after a BFS hop along a pipe of pipe_length_m.
        """
        attenuation = (pipe_length_m / WaterPhysics.MAX_PIPE_LENGTH) * WaterPhysics.BFS_ATTENUATION
        return max(0.0, upstream_pressure * (1.0 - attenuation))

    @staticmethod
    def flow_from_pressure(pressure: float, base_flow: float = 1.0) -> float:
        """Torricelli approximation: flow ∝ sqrt(pressure)."""
        return base_flow * math.sqrt(max(0.0, pressure))

    @staticmethod
    def flood_contamination_step(current_contamination: float,
                                  flood_severity: float) -> float:
        """One tick of flood contamination (logistic growth)."""
        return current_contamination + (
            WaterPhysics.FLOOD_CONTAMINATION_RATE
            * flood_severity
            * (1.0 - current_contamination)
        )


def init_water_node(
    node_id: str,
    node_type: str = "pipe_junction",
    base_flow: float = 0.5,
    pipe_diameter_mm: float = 150.0,
    zone: str = "general",
    near_critical_facility: bool = False,
) -> Dict[str, Any]:
    """
    Return the complete attribute dict for a water node.

    Fields
    ------
    pressure              float   0.0–1.0 (fraction of design operating pressure)
    flow_rate             float   current flow as fraction of base capacity
    base_flow             float   healthy flow rate (before any failure)
    water_level           float   0.0–1.0 for tanks/reservoirs (N/A for junctions)
    contamination_level   float   0.0 = clean, 1.0 = fully contaminated
    is_potable            bool    True if contamination ≤ CONTAMINATION_SAFE
    pump_status           str     "running" / "stopped" / "backup" / "failed"
    pump_runtime_hours    float   hours since last maintenance (age proxy)
    power_source          str     "grid" / "backup_gen" / "solar" / "none"
    pipe_diameter_mm      float   distribution pipe diameter
    pipe_age_years        float   proxy for failure probability (older = fragile)
    zone                  str     supply zone name (e.g. "north", "south")
    near_critical_facility bool   True if within 300m of hospital/fire station
    """
    is_pump = node_type in ("pump_station", "reservoir", "storage_tank")
    crit = WaterPhysics.BASE_CRITICALITY.get(node_type, 2.0)
    if near_critical_facility:
        crit = max(crit, 4.0)   # near-hospital junctions → minimum 4 (TDD §5.2 W3)

    return {
        # ── identity ──────────────────────────────────────────────────────
        "network":               "water",
        "node_id":               node_id,
        "node_type":             node_type,
        "zone":                  zone,
        # ── health state ─────────────────────────────────────────────────
        "health":                1.0,
        "health_history":        [1.0, 1.0, 1.0, 1.0, 1.0],
        # ── hydraulics ───────────────────────────────────────────────────
        "pressure":              1.0,
        "design_pressure":       1.0,      # reference: healthy operating pressure
        "flow_rate":             base_flow,
        "base_flow":             base_flow,
        "water_level":           1.0 if is_pump else None,    # fraction of capacity
        "storage_capacity_m3":   5000.0 if is_pump else None,
        # ── quality ──────────────────────────────────────────────────────
        "contamination_level":   0.0,      # 0 = clean, 1 = contaminated
        "is_potable":            True,
        # ── pump mechanics ───────────────────────────────────────────────
        "pump_status":           "running" if is_pump else None,
        "pump_runtime_hours":    0.0 if is_pump else None,
        "power_source":          "grid" if is_pump else None,
        "has_backup_gen":        False,
        "backup_gen_runtime_h":  0.0,
        # ── infrastructure properties ────────────────────────────────────
        "pipe_diameter_mm":      pipe_diameter_mm,
        "pipe_age_years":        10.0,     # default; can be randomised in Monte Carlo
        "pipe_material":         "PVC",    # PVC / DI / CI — affects failure prob
        # ── facility proximity ───────────────────────────────────────────
        "near_critical_facility": near_critical_facility,
        "criticality":            crit,
        # ── cascade bookkeeping ──────────────────────────────────────────
        "pending_cascades":       [],
        "cascade_description":    "",
        "flood_risk":             False,
        "pop_density":            1.0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  ROAD NETWORK
# ═══════════════════════════════════════════════════════════════════════════════

class RoadPhysics:
    """
    All constants and formulas for the road agent.
    Sources: HCM (Highway Capacity Manual), BBMP road specs, TDD §5.3.

    SIGNAL FAILURE CAPACITY DROP  (Rule R2)
    ----------------------------------------
    When a traffic signal loses power, the intersection degrades to
    "all-way stop" or "yield" behaviour.  Empirically, capacity drops
    to ≈40% of signalised capacity (HCM 2010, unsignalised vs signalised):

        new_capacity_fraction = SIGNAL_FAILURE_CAPACITY
        effective_travel_time = base_travel_time / new_capacity_fraction

    CONGESTION PROPAGATION  (Rule R2, secondary effect)
    ------------------------------------------------------
    Extra vehicles rerouted through an edge increase its effective travel time:

        new_travel_time = base_travel_time × (1 + CONGESTION_FACTOR × extra_flow_fraction)

        CONGESTION_FACTOR = 0.8  (BPR-style, simplified)

    FLOOD NODE: edge blocking
    --------------------------
    When a road node is flooded, all incident edges get:
        blocked = True
        travel_time → ∞ (represented as INFINITY_TRAVEL)

    HOSPITAL ACCESS DEGRADATION THRESHOLD
    ----------------------------------------
        If Dijkstra travel time to any hospital > BASE × 1.5 → emit NODE_DEGRADED
        If no path to any hospital → emit ROUTE_BLOCKED (severity = 1.0)
    """

    # ── signal failure ──────────────────────────────────────────────────────
    SIGNAL_FAILURE_CAPACITY   : float = 0.40   # 40% of signalised capacity
    SIGNAL_TRAVEL_TIME_MULT   : float = 2.50   # 1 / 0.40

    # ── congestion ──────────────────────────────────────────────────────────
    CONGESTION_FACTOR         : float = 0.80   # BPR α parameter (simplified)
    MAX_FLOW_RATIO            : float = 3.0    # cap rerouted flow multiplier

    # ── flood ────────────────────────────────────────────────────────────────
    INFINITY_TRAVEL           : float = 1e9    # effectively impassable

    # ── hospital access alert threshold ─────────────────────────────────────
    HOSPITAL_TIME_DEGRADED_MULT: float = 1.50  # > 150% baseline → DEGRADED
    HOSPITAL_TIME_CRITICAL_MULT: float = 3.00  # > 300% baseline → CRITICAL

    # ── degree threshold for "major intersection" ────────────────────────────
    MAJOR_INTERSECTION_DEGREE : int   = 3

    # ── water on road: flood→water pipe contamination ────────────────────────
    FLOOD_DEPTH_PIPE_THRESHOLD: float = 0.3   # flood severity above which pipes contaminate

    # ── criticality ─────────────────────────────────────────────────────────
    BASE_CRITICALITY : Dict[str, float] = {
        "major_intersection": 3.0,
        "signal_intersection": 3.0,
        "standard":           1.0,
        "near_hospital":      4.0,
        "near_fire_station":  3.0,
    }

    @staticmethod
    def signal_failure_travel_time(base_travel_time: float) -> float:
        """Travel time through an intersection after signal failure."""
        return base_travel_time * RoadPhysics.SIGNAL_TRAVEL_TIME_MULT

    @staticmethod
    def congestion_travel_time(base_travel_time: float,
                               extra_flow_fraction: float) -> float:
        """
        Travel time after absorbing rerouted traffic.
        extra_flow_fraction: additional vehicles as fraction of base volume.
        """
        extra = min(extra_flow_fraction, RoadPhysics.MAX_FLOW_RATIO)
        return base_travel_time * (1.0 + RoadPhysics.CONGESTION_FACTOR * extra)

    @staticmethod
    def is_hospital_access_blocked(
        current_time: float,
        baseline_time: float,
    ) -> tuple:
        """
        Returns (is_blocked: bool, is_degraded: bool, severity: float).
        current_time = Dijkstra result; baseline_time = healthy baseline.
        """
        if current_time >= RoadPhysics.INFINITY_TRAVEL * 0.5:
            return True, True, 1.0
        ratio = current_time / max(baseline_time, 1.0)
        if ratio > RoadPhysics.HOSPITAL_TIME_CRITICAL_MULT:
            return False, True, min(1.0, (ratio - 1.0) / RoadPhysics.HOSPITAL_TIME_CRITICAL_MULT)
        if ratio > RoadPhysics.HOSPITAL_TIME_DEGRADED_MULT:
            return False, True, min(1.0, (ratio - 1.0) / RoadPhysics.HOSPITAL_TIME_DEGRADED_MULT)
        return False, False, 0.0


def init_road_node(
    node_id: str,
    highway_class: str = "unclassified",
    degree: int = 2,
    has_traffic_signal: bool = False,
    near_critical_facility: bool = False,
    baseline_travel_time_s: float = 30.0,
) -> Dict[str, Any]:
    """
    Return the complete attribute dict for a road node.

    Fields
    ------
    passable              bool    False if flooded or physically blocked
    capacity_fraction     float   current capacity (1.0 = full, 0.4 = signal failed)
    congestion_factor     float   multiplier on travel time from rerouted traffic
    travel_time_multiplier float  combined multiplier applied to edge travel times
    flood_depth_m         float   current water depth (m); > 0.3m → impassable
    has_traffic_signal    bool    True if signalised intersection
    signal_operational    bool    False if power lost to signal
    is_major_intersection bool    True if degree ≥ 3
    near_hospital         bool    True if within 300m of hospital
    baseline_travel_time_s float  Dijkstra reference at t=0 (healthy network)
    hospital_access_time_s float  current Dijkstra time to nearest hospital
    emergency_route       bool    True if this node lies on a hospital Dijkstra path
    """
    is_major = degree >= RoadPhysics.MAJOR_INTERSECTION_DEGREE
    if near_critical_facility:
        crit = RoadPhysics.BASE_CRITICALITY["near_hospital"]
    elif has_traffic_signal or is_major:
        crit = RoadPhysics.BASE_CRITICALITY["signal_intersection"]
    else:
        crit = RoadPhysics.BASE_CRITICALITY["standard"]

    return {
        # ── identity ──────────────────────────────────────────────────────
        "network":               "road",
        "node_id":               node_id,
        "highway_class":         highway_class,
        # ── health state ─────────────────────────────────────────────────
        "health":                1.0,
        "health_history":        [1.0, 1.0, 1.0, 1.0, 1.0],
        # ── traversability ───────────────────────────────────────────────
        "passable":              True,
        "capacity_fraction":     1.0,    # 0.0–1.0
        "congestion_factor":     1.0,    # multiplier (1.0 = no congestion)
        "travel_time_multiplier": 1.0,   # capacity × congestion combined
        # ── flood state ──────────────────────────────────────────────────
        "flood_depth_m":         0.0,    # metres of standing water
        "is_flooded":            False,
        # ── signal state ─────────────────────────────────────────────────
        "has_traffic_signal":    has_traffic_signal,
        "signal_operational":    True,   # False after power loss
        "signal_power_source":   "grid",
        # ── intersection geometry ────────────────────────────────────────
        "is_major_intersection": is_major,
        "degree":                degree,
        # ── hospital access ──────────────────────────────────────────────
        "near_critical_facility": near_critical_facility,
        "near_hospital":         near_critical_facility,
        "baseline_travel_time_s": baseline_travel_time_s,
        "hospital_access_time_s": baseline_travel_time_s,
        "emergency_route":       False,   # set by Dijkstra post-processing
        # ── criticality ──────────────────────────────────────────────────
        "criticality":           crit,
        # ── cascade bookkeeping ──────────────────────────────────────────
        "pending_cascades":      [],
        "cascade_description":   "",
        "flood_risk":            False,
        "pop_density":           1.0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  TELECOM NETWORK
# ═══════════════════════════════════════════════════════════════════════════════

class TelecomPhysics:
    """
    All constants and formulas for the telecom agent.
    Sources: TRAI tower specs, 3GPP LTE coverage models, TDD §5.4.

    COVERAGE GAP DETECTION  (Rule T1)
    ----------------------------------
    A node (road or power) is in a coverage gap if:
        distance_to_nearest_alive_tower > COVERAGE_RADIUS_M

        COVERAGE_RADIUS_M = 500 m  (4G LTE macro cell, urban)

    SIGNAL STRENGTH APPROXIMATION  (log-distance path loss model)
    --------------------------------------------------------------
        signal_strength = MAX_SIGNAL_DBM − PATH_LOSS_EXPONENT × 10 × log10(distance_m)
        (free-space model, urban environment)

        MAX_SIGNAL_DBM    = −50 dBm  (1 m reference)
        PATH_LOSS_EXPONENT = 3.5     (urban environment, 3GPP UMa model)
        MINIMUM_SIGNAL    = −110 dBm (below this → no service)

    LATENCY MODEL  (fiber backbone)
    --------------------------------
        latency_ms = BASE_LATENCY_MS + (fiber_length_km × FIBER_LATENCY_MS_PER_KM)

        BASE_LATENCY_MS           = 5.0   ms
        FIBER_LATENCY_MS_PER_KM   = 5.0   ms/km (speed of light in fiber ÷ overhead)

    SCADA CASCADE  (Rule T2)
    -------------------------
    When a power substation enters a coverage gap:
        substation.vulnerability += SCADA_VULN_BUMP  (capped at 1.0)
    The power agent then uses this elevated vulnerability in its per-tick
    failure probability draw.

    PACKET LOSS (degraded coverage, not full gap)
    -----------------------------------------------
        packet_loss_fraction = max(0, (gap_distance − COVERAGE_RADIUS_M) / COVERAGE_RADIUS_M)
        capped at 1.0
    """

    # ── coverage ────────────────────────────────────────────────────────────
    COVERAGE_RADIUS_M          : float = 500.0  # metres
    # 3GPP TR 36.873 UMa model: reference power at 1m ≈ −40 dBm (20W tower)
    # Path loss exponent 2.7 (urban macrocell, NLOS dominant beyond 50m)
    MAX_SIGNAL_DBM             : float = -40.0
    PATH_LOSS_EXPONENT         : float = 2.7
    MINIMUM_SIGNAL_DBM         : float = -110.0  # LTE sensitivity floor

    # ── latency ─────────────────────────────────────────────────────────────
    BASE_LATENCY_MS            : float = 5.0
    FIBER_LATENCY_MS_PER_KM    : float = 5.0
    CRITICAL_LATENCY_MS        : float = 200.0   # above this → SIGNAL_LOSS

    # ── SCADA ────────────────────────────────────────────────────────────────
    SCADA_VULN_BUMP            : float = 0.30    # matches PowerPhysics.SCADA_VULN_BUMP
    MIN_TOWERS_FOR_REDUNDANCY  : int   = 2       # < 2 alive towers in zone → fragile

    # ── packet loss ─────────────────────────────────────────────────────────
    MAX_PACKET_LOSS            : float = 1.0

    # ── criticality ─────────────────────────────────────────────────────────
    BASE_CRITICALITY : Dict[str, float] = {
        "exchange":    9.0,
        "data_center": 8.0,
        "mast":        5.0,
        "tower":       5.0,
        "cabinet":     3.0,
        "terminal":    3.0,
        "default":     4.0,
    }

    @staticmethod
    def signal_strength_dbm(distance_m: float) -> float:
        """Received signal strength at distance_m from tower (log-distance model)."""
        if distance_m <= 1.0:
            return TelecomPhysics.MAX_SIGNAL_DBM
        loss = TelecomPhysics.PATH_LOSS_EXPONENT * 10.0 * math.log10(distance_m)
        return TelecomPhysics.MAX_SIGNAL_DBM - loss

    @staticmethod
    def is_in_coverage_gap(distance_m: float) -> bool:
        return distance_m > TelecomPhysics.COVERAGE_RADIUS_M

    @staticmethod
    def signal_strength_fraction(distance_m: float) -> float:
        """
        Normalised signal strength 0.0–1.0.
        0.0 = at or beyond gap threshold; 1.0 = within 10m of tower.
        """
        dbm = TelecomPhysics.signal_strength_dbm(distance_m)
        span = TelecomPhysics.MAX_SIGNAL_DBM - TelecomPhysics.MINIMUM_SIGNAL_DBM
        return max(0.0, min(1.0, (dbm - TelecomPhysics.MINIMUM_SIGNAL_DBM) / span))

    @staticmethod
    def fiber_latency_ms(fiber_length_m: float) -> float:
        return TelecomPhysics.BASE_LATENCY_MS + (
            fiber_length_m / 1000.0 * TelecomPhysics.FIBER_LATENCY_MS_PER_KM
        )

    @staticmethod
    def packet_loss(distance_m: float) -> float:
        excess = max(0.0, distance_m - TelecomPhysics.COVERAGE_RADIUS_M)
        return min(TelecomPhysics.MAX_PACKET_LOSS,
                   excess / TelecomPhysics.COVERAGE_RADIUS_M)


def init_telecom_node(
    node_id: str,
    node_type: str = "mast",
    operator: str = "unknown",
    coverage_radius_m: float = 500.0,
    source: str = "osm",
) -> Dict[str, Any]:
    """
    Return the complete attribute dict for a telecom node.

    Fields
    ------
    signal_strength       float   0.0–1.0 (fraction of max signal)
    coverage_radius_m     float   radius in metres where this tower provides coverage
    in_coverage_gap       bool    True if this tower itself has lost backhaul
    latency_ms            float   current measured/estimated latency on backhaul
    packet_loss_fraction  float   0.0–1.0; > 0.20 → SIGNAL_LOSS for dependents
    power_source          str     "grid" / "backup_gen" / "solar" / "none"
    has_backup_gen        bool    True if diesel backup available
    backup_gen_runtime_h  float   hours remaining on backup gen
    is_scada_node         bool    True if this tower monitors a power substation
    scada_substations     list    IDs of substations this tower provides SCADA to
    connected_towers      list    IDs of directly connected towers in backbone
    backhaul_ok           bool    True if fiber backhaul is operational
    """
    crit = TelecomPhysics.BASE_CRITICALITY.get(node_type,
                                               TelecomPhysics.BASE_CRITICALITY["default"])
    return {
        # ── identity ──────────────────────────────────────────────────────
        "network":              "telecom",
        "node_id":              node_id,
        "node_type":            node_type,
        "operator":             operator,
        "source":               source,
        # ── health state ─────────────────────────────────────────────────
        "health":               1.0,
        "health_history":       [1.0, 1.0, 1.0, 1.0, 1.0],
        # ── RF coverage ──────────────────────────────────────────────────
        "coverage_radius_m":    coverage_radius_m,
        "signal_strength":      1.0,        # fraction (1.0 = full signal)
        "in_coverage_gap":      False,
        "packet_loss_fraction": 0.0,
        # ── backhaul / fiber ─────────────────────────────────────────────
        "latency_ms":           TelecomPhysics.BASE_LATENCY_MS,
        "backhaul_ok":          True,
        "connected_towers":     [],          # filled by telecom graph edges
        # ── power source ─────────────────────────────────────────────────
        "power_source":         "grid",
        "has_backup_gen":       False,
        "backup_gen_runtime_h": 0.0,
        # ── SCADA links ──────────────────────────────────────────────────
        "is_scada_node":        False,
        "scada_substations":    [],          # power node IDs monitored by this tower
        # ── criticality ──────────────────────────────────────────────────
        "criticality":          crit,
        # ── cascade bookkeeping ──────────────────────────────────────────
        "pending_cascades":     [],
        "cascade_description":  "",
        "flood_risk":           False,
        "pop_density":          1.0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  SHARED CASCADE SCHEDULING HELPER
#     Call this from every agent's cascade handler.
#     It attaches the PendingCascade to the TARGET node so agents can
#     inspect what is queued without scanning the event log.
# ═══════════════════════════════════════════════════════════════════════════════

def schedule_cascade(
    target_node_data: Dict[str, Any],
    cascade: PendingCascade,
) -> None:
    """
    Attach a PendingCascade to a node's pending_cascades list.
    Also updates cascade_description to the worst (earliest) pending cascade.

    Call pattern (inside an agent):
        dep = registry.get_dependency(source_node, target_node)
        if dep and random.random() < dep["failure_probability"]:
            pc = describe_pending_cascade(
                    source_network, source_node,
                    target_network, target_node,
                    EventType.PRESSURE_DROP.value,
                    fire_at_tick = current_tick + delay_ticks,
                    current_tick = current_tick,
                    dep_type     = dep["dep_type"],
                    failure_prob = dep["failure_probability"],
                    cascade_depth= event.cascade_depth + 1,
            )
            schedule_cascade(G_water.nodes[target_node], pc)
    """
    pending = target_node_data.setdefault("pending_cascades", [])
    pending.append(cascade.to_dict())

    # Keep the earliest-firing (most urgent) cascade as the plain-English description
    if pending:
        earliest = min(pending, key=lambda p: p["fire_at_tick"])
        target_node_data["cascade_description"] = earliest["description"]


def pop_due_cascades(
    node_data: Dict[str, Any],
    current_tick: int,
) -> List[Dict[str, Any]]:
    """
    Remove and return all pending cascades whose fire_at_tick ≤ current_tick.
    Call at the start of every agent tick handler.
    """
    due, remaining = [], []
    for pc in node_data.get("pending_cascades", []):
        (due if pc["fire_at_tick"] <= current_tick else remaining).append(pc)
    node_data["pending_cascades"] = remaining
    # Update description
    if remaining:
        earliest = min(remaining, key=lambda p: p["fire_at_tick"])
        node_data["cascade_description"] = earliest["description"]
    else:
        node_data["cascade_description"] = ""
    return due


def update_health_history(node_data: Dict[str, Any]) -> None:
    """Prepend current health to the 5-step history list (drop oldest)."""
    history = node_data.get("health_history", [1.0] * 5)
    history = [node_data.get("health", 1.0)] + history[:4]
    node_data["health_history"] = history


# ═══════════════════════════════════════════════════════════════════════════════
# 7.  ORCHESTRATOR CONSTANTS
#     Centrally defined so the orchestrator never hardcodes weights.
# ═══════════════════════════════════════════════════════════════════════════════

class OrchestratorWeights:
    """
    Criticality weights used in Weighted Failure Rate (WFR) calculation.
    Source: Phase 3 TDD §6.2.

    WFR = sum(criticality_weight[i] for failed nodes i)
          / sum(criticality_weight[j] for all nodes j)

    Score = 100 × (1 − WFR) × RF × CDP

    RF  = Recovery Factor  = 0.7 + 0.3 × recent_recovery_rate
    CDP = Cascade Depth Penalty = 1 / (1 + CDP_SLOPE × max_cascade_depth)
    """

    # ── node type → criticality weight ─────────────────────────────────────
    NODE_WEIGHTS: Dict[str, float] = {
        "hospital":            10.0,
        "pump_station":         9.0,
        "reservoir":            9.0,
        "substation":           8.0,
        "plant":                8.0,
        "fire_station":         7.0,
        "police":               7.0,
        "generator":            7.0,
        "storage_tank":         7.0,
        "exchange":             5.0,
        "data_center":          5.0,
        "pipe_junction_hospital": 4.0,   # pipe_junction near_critical_facility=True
        "signal_intersection":  3.0,
        "major_intersection":   3.0,
        "mast":                 5.0,
        "tower":                5.0,
        "pipe_junction":        2.0,
        "standard":             1.0,
        "default":              1.0,
    }

    # ── recovery factor ─────────────────────────────────────────────────────
    RF_BASE:          float = 0.70
    RF_RECOVERY_COEF: float = 0.30

    # ── cascade depth penalty ────────────────────────────────────────────────
    CDP_SLOPE:        float = 0.15

    @staticmethod
    def get_weight(node_data: Dict[str, Any]) -> float:
        """
        Resolve criticality weight for a node.
        Uses node_type first; falls back to the criticality float on the node.
        """
        nt = node_data.get("node_type", "default")
        if nt == "pipe_junction" and node_data.get("near_critical_facility"):
            nt = "pipe_junction_hospital"
        w = OrchestratorWeights.NODE_WEIGHTS.get(nt)
        if w is None:
            # Fall back to whatever criticality was set in Phase 1
            w = node_data.get("criticality", 1.0)
        return w

    @staticmethod
    def recovery_factor(recent_recovery_rate: float) -> float:
        """recent_recovery_rate: fraction of nodes recovered in last tick."""
        return OrchestratorWeights.RF_BASE + OrchestratorWeights.RF_RECOVERY_COEF * recent_recovery_rate

    @staticmethod
    def cascade_depth_penalty(max_cascade_depth: int) -> float:
        """Sigmoid-shaped penalty; deeper cascades penalise more."""
        return 1.0 / (1.0 + OrchestratorWeights.CDP_SLOPE * max_cascade_depth)

    @staticmethod
    def compute_score(wfr: float,
                      recovery_rate: float,
                      max_cascade_depth: int) -> float:
        """
        Full resilience score formula.
        Returns float in [0, 100].
        """
        rf  = OrchestratorWeights.recovery_factor(recovery_rate)
        cdp = OrchestratorWeights.cascade_depth_penalty(max_cascade_depth)
        return max(0.0, min(100.0, 100.0 * (1.0 - wfr) * rf * cdp))


# ═══════════════════════════════════════════════════════════════════════════════
# 8.  QUICK SELF-TEST  (python node_physics.py)
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("node_physics.py — self-test")
    print("=" * 60)

    # ── Power ────────────────────────────────────────────────────────────────
    pn = init_power_node("PS-CMH", node_type="substation", base_load=0.65)
    assert pn["health"] == 1.0
    assert pn["load"] == 0.65
    assert "pending_cascades" in pn

    load_rush = PowerPhysics.apply_tod_multiplier(0.65, TimeOfDay.RUSH_HOUR)
    assert abs(load_rush - 0.91) < 0.001, f"Expected 0.91, got {load_rush}"

    health_140 = PowerPhysics.overload_health(1.40)
    assert abs(health_140 - 0.60) < 0.001, f"Expected 0.60, got {health_140}"

    survivors = [
        {"current_load": 0.80, "max_load": 1.0},
        {"current_load": 0.50, "max_load": 1.0},
    ]
    new_loads = PowerPhysics.redistribute_load(0.65, survivors)
    assert abs(sum(new_loads) - (0.80 + 0.50 + 0.65)) < 0.001
    print(f"  [✓] Power — load redistribution: {new_loads}")

    # ── Water ────────────────────────────────────────────────────────────────
    wn = init_water_node("WP-MAIN", node_type="pump_station")
    assert wn["pressure"] == 1.0
    assert wn["contamination_level"] == 0.0
    assert wn["pump_status"] == "running"

    p2 = WaterPhysics.decay_pressure(1.0, elapsed_minutes=2.0)
    # 0.95 × exp(−0.085 × 2) ≈ 0.801
    assert 0.78 < p2 < 0.85, f"Expected ~0.80, got {p2}"
    print(f"  [✓] Water — pressure after 2 min power loss: {p2:.4f}")

    p_neighbour = WaterPhysics.propagate_pressure(p2, pipe_length_m=300)
    assert p_neighbour < p2
    print(f"  [✓] Water — downstream pressure after 300m: {p_neighbour:.4f}")

    contamination = WaterPhysics.flood_contamination_step(0.0, flood_severity=0.8)
    assert abs(contamination - 0.20) < 0.001
    print(f"  [✓] Water — flood contamination step: {contamination:.4f}")

    # ── Road ─────────────────────────────────────────────────────────────────
    rn = init_road_node("R-001", has_traffic_signal=True)
    assert rn["passable"] is True
    assert rn["signal_operational"] is True

    t_signal = RoadPhysics.signal_failure_travel_time(30.0)
    assert abs(t_signal - 75.0) < 0.001
    print(f"  [✓] Road — signal failure travel time: {t_signal:.1f}s (was 30s)")

    is_blocked, is_degraded, sev = RoadPhysics.is_hospital_access_blocked(200.0, 60.0)
    assert is_degraded
    print(f"  [✓] Road — hospital access (200s vs 60s baseline): blocked={is_blocked} degraded={is_degraded} sev={sev:.2f}")

    # ── Telecom ──────────────────────────────────────────────────────────────
    tn = init_telecom_node("TC-1", node_type="mast")
    assert tn["health"] == 1.0
    assert tn["signal_strength"] == 1.0

    sig = TelecomPhysics.signal_strength_fraction(200.0)
    # With UMa model (exp=2.7, max=-40dBm): at 200m ≈ -102dBm → fraction ≈ 0.11
    assert 0.05 < sig < 0.25, f"Expected ~0.11 signal fraction at 200m, got {sig}"
    print(f"  [✓] Telecom — signal at 200m: {sig:.3f}")
    gap = TelecomPhysics.is_in_coverage_gap(600.0)
    assert gap is True
    print(f"  [✓] Telecom — 600m > 500m radius → in_coverage_gap: {gap}")

    # ── PendingCascade ───────────────────────────────────────────────────────
    pc = describe_pending_cascade(
        "power", "PS-CMH", "water", "WP-MAIN",
        "PRESSURE_DROP", fire_at_tick=3, current_tick=1,
        dep_type="power_supply", failure_prob=0.95, cascade_depth=1,
    )
    assert "WP-MAIN" in pc.description
    assert pc.fire_at_tick == 3
    print(f"  [✓] PendingCascade: {pc.description}")

    schedule_cascade(wn, pc)
    assert len(wn["pending_cascades"]) == 1
    assert wn["cascade_description"] != ""
    print(f"  [✓] schedule_cascade attached to node, description set")

    due = pop_due_cascades(wn, current_tick=3)
    assert len(due) == 1
    assert len(wn["pending_cascades"]) == 0
    print(f"  [✓] pop_due_cascades: returned {len(due)} due cascade(s)")

    # ── Orchestrator score ────────────────────────────────────────────────────
    score = OrchestratorWeights.compute_score(wfr=0.20, recovery_rate=0.05, max_cascade_depth=3)
    # 100 × (1−0.20) × 0.715 × 0.690 ≈ 39.4 (severe cascade, deep depth)
    assert 35.0 < score < 45.0, f"Expected ~39, got {score}"
    print(f"  [✓] Resilience score (WFR=0.20, recovery=0.05, depth=3): {score:.2f}")

    print("\nAll self-tests passed.")
    print("=" * 60)
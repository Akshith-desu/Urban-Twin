"""
water_agent.py  —  Phase 3
Water sector agent for the Multi-Agent Urban Digital Twin.

Owns the water distribution graph (loaded from graphs/water.json).
Listens to the event bus for power failures (pumps depend on electricity)
and user-initiated failures.
Applies Equations 1-6 every tick and emits cascade events downstream.

Tick duration: 5 real minutes per tick (TICK_DURATION_MINUTES).

FIX (v2): Pump → transformer wiring is now resolved at load time.
  At init, we load power.json, find the nearest transformer to each pump
  (by Euclidean distance in projected coordinates), and store its name as
  pump["power_dependency"]. The FEEDER_LINE_DROPPED handler then matches
  on event.node_id (the transformer that went dark) OR affected_nodes.
"""

import json
import math
import random
import asyncio
import logging
import os
from typing import Dict, List, Optional, Tuple, Any

from event_schema import (
    Event, EventType, Network, TICK_DURATION_MINUTES,
    node_failed, node_degraded, node_recovered,
    cascade_triggered, pump_station_fail, pressure_drop,
    user_fail_node, user_restore_node,
)
from event_bus import EventBus

logger = logging.getLogger(__name__)


# ── physics constants ──────────────────────────────────────────────────────────
K_DECAY               = 0.085   # Equation 1: pump pressure decay per minute
P_MIN                 = 0.05    # Equation 3: zero flow below this
P_DES                 = 1.00    # Equation 3: full flow above this
LOW_PRESSURE_THRESH   = 0.40    # health damage starts below this
CRITICAL_PRESSURE     = 0.20    # cascade emitted to downstream below this
TOWER_DRAIN_THRESH    = 0.20    # tower cascade trigger
HEALTH_FAIL_THRESH    = 0.10    # node considered failed
HEALTH_DEGRADE_THRESH = 0.50    # degraded event threshold
TICKS_FOR_DAMAGE      = 3       # consecutive low-pressure ticks before health drops
LAMBDA_BURST          = 0.15    # Equation 6: base burst rate per tick
CASCADE_SEVERITY_MULT = 1.5     # health below 0.3 → severity multiplier on emissions
DEMAND_MULTIPLIER_FLOOD = 1.8   # Equation 5: higher drain during flood


class WaterAgent:
    """
    Autonomous water sector agent.

    State: in-memory graph of pump_stations, water_towers, pipe_junctions
           loaded from water.json, augmented with runtime state fields.

    Subscriptions:
        FEEDER_LINE_DROPPED  — power agent says a transformer went dark
        USER_FAIL_NODE       — user failed a water node from frontend
        USER_RESTORE_NODE    — user restored a water node
        FLOOD_NODE           — flood event (triggers Equation 6 on junctions)
        FLOOD_CLEARED        — flood receded
        SIMULATION_TICK      — advance one tick

    Publishes:
        PUMP_STATION_FAIL, PUMP_ON_BACKUP, PRESSURE_DROP, PIPE_BURST,
        TOWER_DRAINING, TOWER_EMPTY, NODE_FAILED, NODE_DEGRADED,
        NODE_RECOVERED, CASCADE_TRIGGERED
    """

    SUBSCRIBED_EVENTS = [
        EventType.FEEDER_LINE_DROPPED,
        EventType.USER_FAIL_NODE,
        EventType.USER_RESTORE_NODE,
        EventType.FLOOD_NODE,
        EventType.FLOOD_CLEARED,
        EventType.SIMULATION_TICK,
    ]

    def __init__(self, bus: EventBus,
                 water_json_path: str = "graphs/water.json",
                 power_json_path: str = "graphs/power.json"):
        self.bus  = bus
        self.tick = 0
        self.name = "water_agent"

        # ── load water graph ───────────────────────────────────────────────────
        with open(water_json_path, "r") as f:
            raw = json.load(f)

        self.nodes: Dict[str, Dict] = {}
        for n in raw["nodes"]:
            nid  = str(n["node_id"])
            node = dict(n)

            node.setdefault("health",             1.0)
            node.setdefault("health_history",     [1.0] * 5)
            node.setdefault("operational_status", "normal")
            node.setdefault("flood_risk",         False)
            node.setdefault("flood_severity",     0.0)
            node.setdefault("ticks_low_pressure", 0)

            ntype = node.get("node_type", "")

            if ntype == "pump_station":
                node.setdefault("on_grid_power",          True)
                node.setdefault("pump_status",            "running")
                node.setdefault("power_source",           "grid")
                node.setdefault("has_backup_gen",         True)
                node.setdefault("backup_gen_remaining_h",
                                node.get("backup_gen_runtime_h", 2.0))
                node.setdefault("ticks_since_power_loss", 0)
                node.setdefault("pressure",
                                node.get("initial_pressure", 0.9))
                node.setdefault("power_dependency",       None)

            elif ntype == "water_tower":
                node.setdefault("water_level",
                                node.get("water_level", 0.75))
                node.setdefault("is_draining",    False)
                node.setdefault("pressure",
                                node.get("water_level", 0.75))
                node.setdefault("hydraulic_head_pressure",
                                node.get("water_level", 0.75))

            elif ntype == "pipe_junction":
                node.setdefault("pressure",
                                node.get("initial_pressure", 0.7))
                node.setdefault("burst_occurred", False)

            node.setdefault("flow_rate",          node.get("flow_rate", 0.5))
            # base_flow_rate: the "full demand" baseline that never gets overwritten.
            # flow_rate is the pressure-adjusted value recomputed each tick.
            node["base_flow_rate"] = node.get("flow_rate", 0.5)
            node.setdefault("pending_cascades",   [])
            node.setdefault("cascade_description","")

            self.nodes[nid] = node

        # ── edges ──────────────────────────────────────────────────────────────
        self.edges: Dict[int, Dict] = {}
        self._downstream: Dict[str, List[Tuple[int, str]]] = {}
        self._upstream:   Dict[str, List[Tuple[int, str]]] = {}

        for e in raw["edges"]:
            eid  = int(e["edge_id"])
            edge = dict(e)
            edge.setdefault("health",  1.0)
            edge.setdefault("blocked", False)
            edge.setdefault("burst",   False)
            self.edges[eid] = edge

            fn = str(edge.get("from_node", ""))
            tn = str(edge.get("to_node",   ""))
            self._downstream.setdefault(fn, []).append((eid, tn))
            self._upstream.setdefault(tn, []).append((eid, fn))

        # ── node type sets ─────────────────────────────────────────────────────
        self._pump_ids = {
            nid for nid, n in self.nodes.items()
            if n.get("node_type") == "pump_station"
        }
        self._tower_ids = {
            nid for nid, n in self.nodes.items()
            if n.get("node_type") == "water_tower"
        }
        self._junction_ids = {
            nid for nid, n in self.nodes.items()
            if n.get("node_type") == "pipe_junction"
        }

        # ── wire pumps → nearest transformer (THE FIX) ────────────────────────
        self._wire_pumps_to_transformers(power_json_path)

        # ── flood tracking ─────────────────────────────────────────────────────
        self._flooded_nodes: set = set()

        # ── event queues ───────────────────────────────────────────────────────
        self._queues: Dict[EventType, asyncio.Queue] = {}

        self.event_log: List[Dict] = []
        self.last_report: str = ""

        logger.info(
            f"WaterAgent loaded: {len(self.nodes)} nodes "
            f"({len(self._pump_ids)} pumps, {len(self._tower_ids)} towers, "
            f"{len(self._junction_ids)} junctions), "
            f"{len(self.edges)} edges"
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PUMP → TRANSFORMER WIRING
    # ══════════════════════════════════════════════════════════════════════════

    def _wire_pumps_to_transformers(self, power_json_path: str):
        """
        Load power.json and snap each pump_station to its nearest transformer
        by Euclidean distance (x/y in projected coordinates).

        Stores transformer name in pump["power_dependency"].
        Also builds self._transformer_to_pumps: transformer_name → [pump_ids]
        so the FEEDER_LINE_DROPPED handler can do an O(1) lookup.
        """
        self._transformer_to_pumps: Dict[str, List[str]] = {}

        if not os.path.exists(power_json_path):
            logger.warning(
                f"power.json not found at {power_json_path} — "
                f"pumps will not respond to power failures"
            )
            return

        with open(power_json_path) as pf:
            power_raw = json.load(pf)

        # collect transformer nodes that have coordinates
        transformers = [
            n for n in power_raw["nodes"]
            if str(n.get("power", "")) == "transformer"
            and n.get("x") is not None
            and n.get("y") is not None
        ]

        if not transformers:
            logger.warning("No transformer nodes found in power.json")
            return

        tx_list = [(t["x"], t["y"], t["name"]) for t in transformers]

        wired = 0
        for pump_id in self._pump_ids:
            pump = self.nodes[pump_id]

            # skip if already set (e.g. loaded from JSON)
            if pump.get("power_dependency"):
                dep = pump["power_dependency"]
                self._transformer_to_pumps.setdefault(dep, []).append(pump_id)
                wired += 1
                continue

            px = pump.get("x")
            py = pump.get("y")
            if px is None or py is None:
                logger.warning(f"Pump {pump_id} has no x/y — skipping wiring")
                continue

            # nearest transformer by Euclidean distance
            best_name = None
            best_dist = float("inf")
            for tx, ty, tname in tx_list:
                d = math.sqrt((px - tx) ** 2 + (py - ty) ** 2)
                if d < best_dist:
                    best_dist = d
                    best_name = tname

            if best_name:
                pump["power_dependency"] = best_name
                self._transformer_to_pumps.setdefault(best_name, []).append(pump_id)
                wired += 1
                logger.info(
                    f"  Pump {pump_id} ({pump.get('name')}) "
                    f"→ transformer '{best_name}' ({best_dist:.0f} m)"
                )

        logger.info(
            f"Pump wiring complete: {wired}/{len(self._pump_ids)} pumps wired "
            f"to transformers. Transformer→pump map: "
            f"{ {k: v for k, v in self._transformer_to_pumps.items()} }"
        )

    # ══════════════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ══════════════════════════════════════════════════════════════════════════

    async def start(self):
        self._queues = self.bus.subscribe(self.name, self.SUBSCRIBED_EVENTS)
        logger.info(f"{self.name} started")
        await self._run()

    async def _run(self):
        while True:
            await self._drain_queues()
            await asyncio.sleep(0.005)

    async def _drain_queues(self):
        for et, q in self._queues.items():
            while not q.empty():
                event: Event = q.get_nowait()
                await self._handle(event)

    # ══════════════════════════════════════════════════════════════════════════
    # EVENT HANDLER
    # ══════════════════════════════════════════════════════════════════════════

    async def _handle(self, event: Event):

        if event.event_type == EventType.SIMULATION_TICK:
            self.tick = event.tick
            await self._on_tick()

        elif event.event_type == EventType.FEEDER_LINE_DROPPED:
            await self._handle_feeder_dropped(event)

        elif event.event_type == EventType.USER_FAIL_NODE:
            if event.source_network == Network.WATER:
                await self._fail_node(event.node_id, reason="user_triggered",
                                      cascade_depth=0)

        elif event.event_type == EventType.USER_RESTORE_NODE:
            if event.source_network == Network.WATER:
                await self._restore_node(event.node_id)

        elif event.event_type == EventType.FLOOD_NODE:
            node_id = event.node_id
            self._flooded_nodes.add(node_id)
            if node_id in self.nodes:
                self.nodes[node_id]["flood_risk"] = True
                # store actual flood severity from event for Equation 6
                sev = event.metadata.get("flood_severity", event.severity)
                self.nodes[node_id]["flood_severity"] = max(
                    self.nodes[node_id].get("flood_severity", 0.0), sev
                )
                # ── Pump station flooding ──────────────────────────────
                # Ground-level electrical equipment (motors, switchgear,
                # control panels) is destroyed when submerged.
                ntype = self.nodes[node_id].get("node_type", "")
                if ntype == "pump_station" and sev >= 0.5:
                    pump = self.nodes[node_id]
                    if pump.get("pump_status") != "failed":
                        logger.info(
                            f"[tick {self.tick}] Pump {node_id} flooded "
                            f"(severity={sev:.2f}) — electrical equipment "
                            f"submerged, pump destroyed"
                        )
                        await self._fail_node(
                            node_id, reason="flood_submerged",
                            cascade_depth=0
                        )

        elif event.event_type == EventType.FLOOD_CLEARED:
            node_id = event.node_id
            self._flooded_nodes.discard(node_id)
            if node_id in self.nodes:
                self.nodes[node_id]["flood_risk"] = False
                self.nodes[node_id]["flood_severity"] = 0.0

    async def _handle_feeder_dropped(self, event: Event):
        """
        Power agent emitted FEEDER_LINE_DROPPED.
        event.node_id   = the transformer name that went dark (e.g. "Transformer 3")
        event.affected_nodes = list of building IDs that lost power

        We match pumps three ways (most → least direct):
          1. Direct lookup via _transformer_to_pumps[event.node_id]   ← primary path
          2. pump["power_dependency"] in event.affected_nodes          ← building match
          3. pump_id itself in event.affected_nodes                    ← fallback
        """
        failed_transformer_id = event.node_id
        failed_transformer_name = event.metadata.get("transformer_name", failed_transformer_id)
        affected_set = set(event.affected_nodes)

        # path 1: O(1) lookup — this fires when our wiring is correct
        direct_pumps = self._transformer_to_pumps.get(failed_transformer_name, [])
        if not direct_pumps:
            direct_pumps = self._transformer_to_pumps.get(failed_transformer_id, [])
            
        for pump_id in direct_pumps:
            pump = self.nodes.get(pump_id)
            if pump and pump.get("on_grid_power", True):
                logger.info(
                    f"[tick {self.tick}] Pump {pump_id} lost power "
                    f"(transformer '{failed_transformer_name}' dropped)"
                )
                await self._pump_lose_grid_power(
                    pump_id, cascade_depth=event.cascade_depth + 1
                )

        # path 2 + 3: scan remaining pumps not already caught by direct lookup
        direct_set = set(direct_pumps)
        for pump_id in self._pump_ids:
            if pump_id in direct_set:
                continue  # already handled above
            pump = self.nodes[pump_id]
            if not pump.get("on_grid_power", True):
                continue  # already off grid
            power_dep = pump.get("power_dependency", "")
            if power_dep in affected_set or pump_id in affected_set:
                logger.info(
                    f"[tick {self.tick}] Pump {pump_id} lost power "
                    f"(matched via affected_nodes)"
                )
                await self._pump_lose_grid_power(
                    pump_id, cascade_depth=event.cascade_depth + 1
                )

    # ══════════════════════════════════════════════════════════════════════════
    # TICK PROCESSING
    # ══════════════════════════════════════════════════════════════════════════

    async def _on_tick(self):
        await self._step_pump_decay()
        await self._step_tower_drain()
        await self._step_pressure_propagation()
        await self._step_flow_recompute()
        await self._step_pipe_burst()
        await self._step_health_and_cascade()
        self._generate_report_stub()

    # ── Equation 1: Pump pressure decay ───────────────────────────────────────
    async def _step_pump_decay(self):
        for pump_id in self._pump_ids:
            pump = self.nodes[pump_id]
            if pump.get("pump_status") == "failed":
                continue

            if pump.get("on_grid_power", True):
                pump["ticks_since_power_loss"] = 0
                continue

            # off grid — check backup gen
            backup_h = pump.get("backup_gen_remaining_h", 0.0)
            if backup_h > 0:
                pump["pump_status"]  = "on_backup"
                pump["power_source"] = "backup"
                backup_h -= TICK_DURATION_MINUTES / 60.0
                pump["backup_gen_remaining_h"] = max(0.0, round(backup_h, 3))
                if pump["backup_gen_remaining_h"] <= 0:
                    pump["power_source"] = "none"
                    logger.info(
                        f"[tick {self.tick}] Pump {pump_id} backup exhausted"
                    )
            else:
                # no backup — Equation 1 decay
                pump["power_source"] = "none"
                t_min  = pump.get("ticks_since_power_loss", 0) * TICK_DURATION_MINUTES
                P0     = pump.get("initial_pressure", 0.9)
                new_p  = P0 * math.exp(-K_DECAY * t_min)
                old_p  = pump.get("pressure", P0)
                pump["pressure"] = round(max(0.0, new_p), 4)
                pump["ticks_since_power_loss"] = (
                    pump.get("ticks_since_power_loss", 0) + 1
                )

                if new_p <= 0.01 and old_p > 0.01:
                    pump["pump_status"] = "failed"
                    await self._on_pump_failed(pump_id)

    # ── Equation 4 + 5: Tower hydraulic head and drain rate ───────────────────
    async def _step_tower_drain(self):
        for tower_id in self._tower_ids:
            tower = self.nodes[tower_id]
            if tower.get("operational_status") == "failed":
                continue

            fill_src  = tower.get("fill_source_node")
            src_alive = (
                fill_src
                and fill_src in self.nodes
                and self.nodes[fill_src].get("pump_status") == "running"
            )

            if not src_alive:
                tower["is_draining"] = True
                base_flow   = tower.get("flow_rate", 0.3)
                storage     = max(tower.get("storage_capacity_m3", 1000), 1)
                demand_mult = DEMAND_MULTIPLIER_FLOOD if tower.get("flood_risk") else 1.0
                drain       = (base_flow * demand_mult) / storage
                old_level   = tower.get("water_level", 0.5)
                new_level   = max(0.0, round(old_level - drain, 4))
                tower["water_level"] = new_level

                if new_level <= TOWER_DRAIN_THRESH and old_level > TOWER_DRAIN_THRESH:
                    await self._publish(Event(
                        EventType.TOWER_DRAINING, Network.WATER, tower_id,
                        severity=round(1.0 - new_level, 3),
                        tick=self.tick,
                        metadata={"water_level": new_level,
                                   "fill_source": fill_src}
                    ))

                if new_level <= 0.0 and old_level > 0.0:
                    # ── Tower empty cascade ─────────────────────────────
                    # Tower can no longer provide hydraulic head pressure.
                    # Mark failed so BFS won't seed from it, and cascade
                    # pressure loss to downstream junctions.
                    tower["operational_status"] = "failed"
                    tower["pressure"]           = 0.0
                    tower["hydraulic_head_pressure"] = 0.0

                    await self._publish(Event(
                        EventType.TOWER_EMPTY, Network.WATER, tower_id,
                        severity=1.0, tick=self.tick,
                        metadata={"fill_source": fill_src}
                    ))
                    await self._publish(node_failed(
                        Network.WATER, tower_id, self.tick,
                        reason="tower_empty",
                        cascade_depth=1
                    ))

                    # Emit cascade events for each downstream junction so
                    # the event log shows the full impact chain
                    for eid, dn_id in self._downstream.get(tower_id, []):
                        if dn_id in self.nodes:
                            await self._publish(cascade_triggered(
                                Network.WATER, tower_id,
                                Network.WATER, dn_id,
                                tick=self.tick, depth=2,
                                reason="tower_empty_pressure_loss"
                            ))
            else:
                tower["is_draining"] = False

            # Equation 4
            wl = tower.get("water_level", 0.0)
            tower["hydraulic_head_pressure"] = round(wl, 4)
            tower["pressure"]                = round(wl, 4)

    # ── Equation 2: Pressure propagation (Hazen-Williams BFS) ─────────────────
    async def _step_pressure_propagation(self):
        from collections import deque

        visited = set()
        queue   = deque()

        for pump_id in self._pump_ids:
            pump = self.nodes[pump_id]
            if pump.get("pump_status") != "failed":
                queue.append((pump_id, pump.get("pressure", 0.0)))
                visited.add(pump_id)

        for tower_id in self._tower_ids:
            tower = self.nodes[tower_id]
            if tower.get("water_level", 0) > 0:
                queue.append((tower_id, tower.get("hydraulic_head_pressure", 0.0)))
                visited.add(tower_id)

        while queue:
            current_id, upstream_pressure = queue.popleft()

            for eid, downstream_id in self._downstream.get(current_id, []):
                edge = self.edges[eid]
                if edge.get("blocked") or edge.get("burst"):
                    continue
                if downstream_id not in self.nodes:
                    continue

                loss_frac    = edge.get("loss_fraction", 0.01)
                new_pressure = round(max(0.0, upstream_pressure * (1.0 - loss_frac)), 4)

                dn_node = self.nodes[downstream_id]
                if downstream_id not in visited or new_pressure > dn_node.get("pressure", 0):
                    dn_node["pressure"] = new_pressure
                    if downstream_id not in visited:
                        visited.add(downstream_id)
                        queue.append((downstream_id, new_pressure))

        for nid, node in self.nodes.items():
            if nid not in visited:
                node["pressure"] = 0.0
            # burst junctions can't hold pressure — water leaks out
            if node.get("burst_occurred"):
                node["pressure"] = 0.0

    # ── Equation 3: Pressure-driven demand (Wagner model) ─────────────────────
    async def _step_flow_recompute(self):
        for nid, node in self.nodes.items():
            P      = node.get("pressure", 0.0)
            # Use base_flow_rate (the original full-demand baseline) — NOT flow_rate,
            # which was already pressure-adjusted by a previous tick.
            q_full = node.get("base_flow_rate", node.get("flow_rate", 0.5))

            if P <= P_MIN:
                node["flow_rate"] = 0.0
            elif P >= P_DES:
                node["flow_rate"] = round(q_full, 4)
            else:
                ratio = (P - P_MIN) / (P_DES - P_MIN)
                node["flow_rate"] = round(q_full * math.sqrt(max(0.0, ratio)), 4)

    # ── Equation 6: Pipe burst probability under flood ─────────────────────────
    async def _step_pipe_burst(self):
        for junc_id in self._junction_ids:
            node = self.nodes[junc_id]
            if not node.get("flood_risk"):
                continue
            if node.get("burst_occurred"):
                continue

            # read per-node flood severity (set by FLOOD_NODE handler)
            flood_severity = node.get("flood_severity", 0.5)
            age_factor = node.get("pipe_age_years", 15.0) / 20.0
            burst_prob = 1.0 - math.exp(-LAMBDA_BURST * flood_severity * age_factor)

            if random.random() < burst_prob:
                node["burst_occurred"] = True
                node["pressure"]       = 0.0
                health_drop = random.uniform(0.4, 0.6)
                node["health"] = max(0.0, node.get("health", 1.0) - health_drop)

                logger.info(
                    f"[tick {self.tick}] PIPE BURST at {junc_id} "
                    f"(age={node.get('pipe_age_years')}yr, p={burst_prob:.3f})"
                )

                await self._publish(Event(
                    EventType.PIPE_BURST, Network.WATER, junc_id,
                    severity=round(health_drop, 3),
                    tick=self.tick,
                    metadata={
                        "burst_probability": round(burst_prob, 4),
                        "pipe_age_years":    node.get("pipe_age_years"),
                        "flood_severity":    flood_severity,
                    }
                ))
                node["cascade_description"] = (
                    f"Pipe burst at tick {self.tick} "
                    f"(flood severity {flood_severity:.1f}, "
                    f"age {node.get('pipe_age_years')}yr)"
                )

                for eid, dn_id in self._downstream.get(junc_id, []):
                    self.edges[eid]["burst"]   = True
                    self.edges[eid]["blocked"] = True

    # ── Health updates + cascade emissions ────────────────────────────────────
    async def _step_health_and_cascade(self):
        for nid, node in self.nodes.items():
            old_health = node.get("health", 1.0)
            pressure   = node.get("pressure", 0.0)

            if pressure < LOW_PRESSURE_THRESH:
                node["ticks_low_pressure"] = node.get("ticks_low_pressure", 0) + 1
            else:
                node["ticks_low_pressure"] = 0

            if node["ticks_low_pressure"] > TICKS_FOR_DAMAGE:
                node["health"] = max(0.0, old_health - 0.08)

            history = node.get("health_history", [1.0] * 5)
            node["health_history"] = history[-4:] + [node["health"]]

            new_health = node["health"]

            if new_health <= HEALTH_FAIL_THRESH and old_health > HEALTH_FAIL_THRESH:
                node["operational_status"] = "failed"
                await self._publish(node_failed(Network.WATER, nid, self.tick,
                                                reason="sustained_low_pressure"))

            elif new_health <= HEALTH_DEGRADE_THRESH and old_health > HEALTH_DEGRADE_THRESH:
                node["operational_status"] = "degraded"
                await self._publish(node_degraded(Network.WATER, nid, self.tick,
                                                   severity=round(1.0 - new_health, 2)))

            if 0 < pressure < CRITICAL_PRESSURE:
                sev_mult = CASCADE_SEVERITY_MULT if new_health < 0.3 else 1.0
                for eid, dn_id in self._downstream.get(nid, []):
                    if self.edges[eid].get("blocked"):
                        continue
                    await self._publish(pressure_drop(
                        dn_id, self.tick,
                        pressure=pressure,
                        cascade_depth=1,
                        upstream_node=nid,
                        severity_multiplier=sev_mult,
                    ))
                    node["pending_cascades"].append(dn_id)

            node["pending_cascades"] = []

    # ══════════════════════════════════════════════════════════════════════════
    # PUMP POWER LOSS HANDLING
    # ══════════════════════════════════════════════════════════════════════════

    async def _pump_lose_grid_power(self, pump_id: str, cascade_depth: int):
        pump = self.nodes[pump_id]
        pump["on_grid_power"]          = False
        pump["ticks_since_power_loss"] = 0

        backup_h = pump.get("backup_gen_remaining_h", 0.0)
        if backup_h > 0:
            pump["pump_status"]  = "on_backup"
            pump["power_source"] = "backup"
            logger.info(
                f"[tick {self.tick}] Pump {pump_id} lost grid power, "
                f"switching to backup ({backup_h:.1f}h remaining)"
            )
            await self._publish(Event(
                EventType.PUMP_ON_BACKUP, Network.WATER, pump_id,
                severity=0.3, tick=self.tick,
                metadata={"backup_hours_remaining": backup_h,
                           "cascade_depth": cascade_depth}
            ))
        else:
            pump["pump_status"]  = "failed"
            pump["power_source"] = "none"
            await self._on_pump_failed(pump_id, cascade_depth=cascade_depth)

    async def _on_pump_failed(self, pump_id: str, cascade_depth: int = 1):
        pump = self.nodes[pump_id]
        pump["pump_status"]        = "failed"
        pump["operational_status"] = "failed"
        pump["pressure"]           = 0.0

        affected_towers = [
            t_id for t_id in self._tower_ids
            if self.nodes[t_id].get("fill_source_node") == pump_id
        ]

        logger.info(
            f"[tick {self.tick}] Pump {pump_id} FAILED — "
            f"{len(affected_towers)} towers start draining"
        )

        await self._publish(pump_station_fail(
            pump_id, self.tick,
            reason="power_lost",
            affected_towers=affected_towers,
        ))

        for tower_id in affected_towers:
            self.nodes[tower_id]["is_draining"] = True

    # ══════════════════════════════════════════════════════════════════════════
    # FAIL / RESTORE
    # ══════════════════════════════════════════════════════════════════════════

    async def _fail_node(self, node_id: str, reason: str = "unknown",
                         cascade_depth: int = 0):
        if node_id not in self.nodes:
            return
        node  = self.nodes[node_id]
        ntype = node.get("node_type", "")

        node["health"]             = 0.0
        node["operational_status"] = "failed"
        node["pressure"]           = 0.0

        if ntype == "pump_station":
            node["pump_status"]  = "failed"
            node["power_source"] = "none"
            await self._on_pump_failed(node_id, cascade_depth)
        elif ntype == "pipe_junction":
            node["burst_occurred"] = True
            for eid, _ in self._downstream.get(node_id, []):
                self.edges[eid]["blocked"] = True

        await self._publish(node_failed(Network.WATER, node_id, self.tick,
                                        cascade_depth=cascade_depth,
                                        reason=reason))

    async def _restore_node(self, node_id: str):
        if node_id not in self.nodes:
            return
        node  = self.nodes[node_id]
        ntype = node.get("node_type", "")

        node["health"]             = 1.0
        node["operational_status"] = "normal"
        node["ticks_low_pressure"] = 0
        node["pending_cascades"]   = []

        if ntype == "pump_station":
            node["on_grid_power"]          = True
            node["pump_status"]            = "running"
            node["power_source"]           = "grid"
            node["ticks_since_power_loss"] = 0
            node["pressure"]               = node.get("initial_pressure", 0.9)
        elif ntype == "water_tower":
            node["is_draining"] = False
        elif ntype == "pipe_junction":
            node["burst_occurred"] = False
            for eid, _ in self._downstream.get(node_id, []):
                self.edges[eid]["blocked"] = False
                self.edges[eid]["burst"]   = False

        await self._publish(node_recovered(Network.WATER, node_id, self.tick,
                                           reason="user_restore"))

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLISH
    # ══════════════════════════════════════════════════════════════════════════

    async def _publish(self, event: Event):
        event.tick = self.tick
        await self.bus.publish(event)
        self.event_log.append(event.to_dict())

    # ══════════════════════════════════════════════════════════════════════════
    # REPORT STUB
    # ══════════════════════════════════════════════════════════════════════════

    def _generate_report_stub(self):
        failed_pumps = [p for p in self._pump_ids
                        if self.nodes[p].get("pump_status") == "failed"]
        draining     = [t for t in self._tower_ids
                        if self.nodes[t].get("is_draining")]
        burst_junc   = [j for j in self._junction_ids
                        if self.nodes[j].get("burst_occurred")]
        self.last_report = (
            f"[tick {self.tick} | {self.tick * TICK_DURATION_MINUTES:.0f} min] "
            f"Water: {len(failed_pumps)} pumps failed, "
            f"{len(draining)} towers draining, "
            f"{len(burst_junc)} burst junctions. "
            f"LLM report stub — integrate API here."
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════════════════

    def get_node_state(self, node_id: str) -> Optional[Dict]:
        return self.nodes.get(node_id)

    def get_all_states(self) -> Dict[str, Dict]:
        return {
            nid: {
                "health":               n.get("health", 1.0),
                "operational_status":   n.get("operational_status", "normal"),
                "pressure":             n.get("pressure", 0.0),
                "flow_rate":            n.get("flow_rate", 0.0),
                "water_level":          n.get("water_level"),
                "pump_status":          n.get("pump_status"),
                "is_draining":          n.get("is_draining"),
                "burst_occurred":       n.get("burst_occurred", False),
                "flood_risk":           n.get("flood_risk", False),
                "node_type":            n.get("node_type", "unknown"),
                "name":                 n.get("name", nid),
                "cascade_description":  n.get("cascade_description", ""),
                "power_dependency":     n.get("power_dependency"),
            }
            for nid, n in self.nodes.items()
        }

    def get_failed_nodes(self) -> List[str]:
        return [nid for nid, n in self.nodes.items()
                if n.get("health", 1.0) <= HEALTH_FAIL_THRESH]

    def summary(self) -> Dict:
        failed_pumps = [p for p in self._pump_ids
                        if self.nodes[p].get("pump_status") == "failed"]
        on_backup    = [p for p in self._pump_ids
                        if self.nodes[p].get("pump_status") == "on_backup"]
        low_towers   = [t for t in self._tower_ids
                        if self.nodes[t].get("water_level", 1.0) < TOWER_DRAIN_THRESH]
        bursts       = [j for j in self._junction_ids
                        if self.nodes[j].get("burst_occurred")]
        return {
            "network":          "water",
            "tick":             self.tick,
            "total_nodes":      len(self.nodes),
            "pumps_failed":     len(failed_pumps),
            "pumps_on_backup":  len(on_backup),
            "towers_low":       len(low_towers),
            "pipe_bursts":      len(bursts),
            "failed_pump_ids":  failed_pumps,
            "low_tower_ids":    low_towers,
            "burst_ids":        bursts,
            "report":           self.last_report,
        }
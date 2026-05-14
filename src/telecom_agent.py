"""
telecom_agent.py  —  Phase 3
Telecom sector agent for the Multi-Agent Urban Digital Twin.

Owns the telecom infrastructure graph (loaded from graphs/telecom.json).
Listens to the event bus for power failures and user-initiated failures.
Applies battery drain physics and FSPL-based coverage degradation every tick.
Publishes state-change events back to the bus.

Tick duration: 5 real minutes per tick (TICK_DURATION_MINUTES).

Cascade logic:
  Step 1 — FEEDER_LINE_DROPPED (from power agent) or USER_FAIL_NODE
           → tower loses grid power
  Step 2 — Tower switches to battery backup
           battery_remaining_kwh -= power_consumption_kw × (tick_duration_min / 60)
  Step 3 — When battery depletes → CELL_TOWER_FAILED emitted
           All providers on that tower → SIGNAL_LOSS emitted per provider
  Step 4 — Coverage radius degrades proportionally to battery fraction
           (signal degrades before full failure — partial coverage loss)
  Step 5 — FLOOD_NODE → ground towers at risk (50% fail probability),
           wall towers not affected (elevated, sealed enclosures)

Cross-network inputs:
  FEEDER_LINE_DROPPED  ← power agent (tower loses grid power)
  FLOOD_NODE           ← scenario engine / orchestrator

Cross-network outputs:
  CELL_TOWER_FAILED   → orchestrator (coverage gap)
  SIGNAL_LOSS         → orchestrator (per-provider outage)
  CELL_TOWER_BATTERY  → orchestrator / dashboard (battery status)
"""

import json
import math
import os
import asyncio
import logging
from typing import Dict, List, Optional, Any

from event_schema import (
    Event, EventType, Network, TICK_DURATION_MINUTES,
    node_failed, node_degraded, node_recovered,
    cell_tower_battery, cell_tower_failed,
    user_fail_node, user_restore_node, sim_tick,
)
from event_bus import EventBus

logger = logging.getLogger(__name__)


# ── physics / threshold constants ─────────────────────────────────────────────
BATTERY_DEGRADE_COVERAGE_THRESHOLD = 0.50   # below 50% battery → coverage starts shrinking
COVERAGE_DEGRADE_FLOOR              = 0.30   # coverage radius never drops below 30% of max
HEALTH_FAIL_THRESHOLD               = 0.10
HEALTH_DEGRADE_THRESHOLD            = 0.50
FLOOD_GROUND_FAIL_PROB              = 0.50   # ground towers: 50% chance on flood
# Wall towers are elevated/sealed — not directly vulnerable to ground flooding


class TelecomAgent:
    """
    Autonomous telecom sector agent.

    State: in-memory dict of all tower nodes loaded from telecom.json.
    Each tower has a nested 'battery' dict and a 'providers' list.
    Both are mutated in place each tick.

    Subscriptions:
        FEEDER_LINE_DROPPED  — power agent says a transformer went dark
        USER_FAIL_NODE       — user failed a telecom node from frontend
        USER_RESTORE_NODE    — user restored a telecom node
        FLOOD_NODE           — flood event (ground towers at risk)
        FLOOD_CLEARED        — flood receded
        SIMULATION_TICK      — advance one tick

    Publishes:
        CELL_TOWER_BATTERY, CELL_TOWER_FAILED, SIGNAL_LOSS,
        NODE_FAILED, NODE_DEGRADED, NODE_RECOVERED
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
                 telecom_json_path: str = "graphs/telecom.json",
                 power_json_path:   str = "graphs/power.json"):
        self.bus  = bus
        self.tick = 0
        self.name = "telecom_agent"

        # ── load telecom graph ─────────────────────────────────────────────────
        with open(telecom_json_path, "r") as f:
            raw = json.load(f)

        # nodes keyed by str(node_id)
        self.nodes: Dict[str, Dict] = {}

        for n in raw["nodes"]:
            nid  = str(n["node_id"])
            node = dict(n)

            # runtime state fields not stored in JSON
            node.setdefault("health",             1.0)
            node.setdefault("operational_status", "normal")
            node.setdefault("on_grid_power",      True)
            node.setdefault("flood_risk",         False)
            node.setdefault("power_dependency",   None)  # nearest transformer name

            # battery dict must be present — telecom.json includes it
            if "battery" not in node:
                # fallback if telecom.json was built without battery field
                kw  = node.get("power_consumption_kw", 4.0)
                hrs = 6.0
                node["battery"] = {
                    "capacity_kwh":     round(kw * hrs, 2),
                    "remaining_kwh":    round(kw * hrs, 2),
                    "backup_hours":     hrs,
                    "on_battery":       False,
                }

            # track original (full-power) coverage radii per provider so we
            # can scale them down during battery degradation
            for p in node.get("providers", []):
                p.setdefault("max_coverage_radius_m",
                             p.get("coverage_radius_m", 500.0))
                p.setdefault("active", True)

            self.nodes[nid] = node

        # ── wire towers → nearest transformer (mirrors water_agent pattern) ───
        self._transformer_to_towers: Dict[str, List[str]] = {}
        self._wire_towers_to_transformers(power_json_path)

        # ── event queues ───────────────────────────────────────────────────────
        self._queues: Dict[EventType, asyncio.Queue] = {}

        self.event_log: List[Dict] = []
        self.last_report: str = ""

        logger.info(
            f"TelecomAgent loaded: {len(self.nodes)} towers"
        )

    # ══════════════════════════════════════════════════════════════════════════
    # TOWER → TRANSFORMER WIRING
    # ══════════════════════════════════════════════════════════════════════════

    def _wire_towers_to_transformers(self, power_json_path: str):
        """
        Load power.json and snap each tower to its nearest transformer by
        Euclidean distance in projected (x/y) coordinates.

        Stores transformer name in tower["power_dependency"].
        Builds self._transformer_to_towers: transformer_name → [tower_ids]
        for O(1) lookup in the FEEDER_LINE_DROPPED handler.

        Mirrors _wire_pumps_to_transformers() in water_agent.py exactly.
        """
        if not os.path.exists(power_json_path):
            logger.warning(
                f"power.json not found at {power_json_path} — "
                f"towers will not respond to power failures"
            )
            return

        with open(power_json_path) as pf:
            power_raw = json.load(pf)

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
        for nid, tower in self.nodes.items():
            # skip if already set
            if tower.get("power_dependency"):
                dep = tower["power_dependency"]
                self._transformer_to_towers.setdefault(dep, []).append(nid)
                wired += 1
                continue

            # telecom.json stores lat/lon, not projected x/y
            # convert using the same CRS as power.json (EPSG:32643 UTM zone 43N)
            lat = tower.get("latitude")
            lon = tower.get("longitude")
            if lat is None or lon is None:
                logger.warning(f"Tower {nid} has no lat/lon — skipping wiring")
                continue

            # simple degree-to-metre approximation is enough for nearest-neighbour;
            # we use actual projected coords from power.json on the transformer side,
            # so we need to project the tower coords too.
            try:
                from pyproj import Transformer as ProjTransformer
                proj = ProjTransformer.from_crs(
                    "EPSG:4326", "EPSG:32643", always_xy=True
                )
                tx_tower, ty_tower = proj.transform(lon, lat)
            except Exception:
                # pyproj unavailable — fall back to degree-based distance
                # (good enough for nearest-neighbour at 2km scale)
                tx_tower = lon * 111320 * math.cos(math.radians(lat))
                ty_tower = lat * 111320

            best_name = None
            best_dist = float("inf")
            for tx, ty, tname in tx_list:
                d = math.sqrt((tx_tower - tx) ** 2 + (ty_tower - ty) ** 2)
                if d < best_dist:
                    best_dist = d
                    best_name = tname

            if best_name:
                tower["power_dependency"] = best_name
                self._transformer_to_towers.setdefault(best_name, []).append(nid)
                wired += 1
                logger.info(
                    f"  Tower {nid} ({tower.get('name')}) "
                    f"→ transformer '{best_name}' ({best_dist:.0f} m)"
                )

        logger.info(
            f"Tower wiring complete: {wired}/{len(self.nodes)} towers wired "
            f"to transformers."
        )

    # ══════════════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ══════════════════════════════════════════════════════════════════════════

    async def start(self):
        """Register subscriptions and start the agent loop."""
        self._queues = self.bus.subscribe(self.name, self.SUBSCRIBED_EVENTS)
        logger.info(f"{self.name} started")
        await self._run()

    # ══════════════════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ══════════════════════════════════════════════════════════════════════════

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
            if event.source_network == Network.TELECOM:
                await self._fail_node(event.node_id, reason="user_triggered",
                                      cascade_depth=0)

        elif event.event_type == EventType.USER_RESTORE_NODE:
            if event.source_network == Network.TELECOM:
                await self._restore_node(event.node_id)

        elif event.event_type == EventType.FLOOD_NODE:
            node_id = event.node_id
            if node_id in self.nodes:
                self.nodes[node_id]["flood_risk"] = True
                tower = self.nodes[node_id]
                # only ground towers are flood-vulnerable
                if tower.get("tower_type") == "ground":
                    import random
                    if random.random() < FLOOD_GROUND_FAIL_PROB:
                        logger.info(
                            f"[tick {self.tick}] Tower {node_id} flooded — "
                            f"ground equipment submerged"
                        )
                        await self._fail_node(node_id, reason="flood",
                                              cascade_depth=0)

        elif event.event_type == EventType.FLOOD_CLEARED:
            if event.node_id in self.nodes:
                self.nodes[event.node_id]["flood_risk"] = False

    async def _handle_feeder_dropped(self, event: Event):
        """
        Power agent emitted FEEDER_LINE_DROPPED.
        event.node_id       = transformer name that went dark
        event.affected_nodes = building IDs that lost power

        Match towers three ways (mirrors water_agent._handle_feeder_dropped):
          1. Direct O(1) lookup via _transformer_to_towers[event.node_id]
          2. tower["power_dependency"] in event.affected_nodes
          3. tower node_id itself in event.affected_nodes (fallback)
        """
        failed_transformer_id = event.node_id
        failed_transformer_name = event.metadata.get("transformer_name", failed_transformer_id)
        affected_set = set(event.affected_nodes)

        # path 1: direct transformer → towers lookup
        direct_towers = self._transformer_to_towers.get(failed_transformer_name, [])
        if not direct_towers:
            direct_towers = self._transformer_to_towers.get(failed_transformer_id, [])
            
        for tower_id in direct_towers:
            tower = self.nodes.get(tower_id)
            if tower and tower.get("on_grid_power", True):
                logger.info(
                    f"[tick {self.tick}] Tower {tower_id} lost grid power "
                    f"(transformer '{failed_transformer_name}' dropped)"
                )
                await self._tower_lose_grid_power(
                    tower_id, cascade_depth=event.cascade_depth + 1
                )

        # path 2 + 3: scan remaining towers not already handled
        direct_set = set(direct_towers)
        for tower_id, tower in self.nodes.items():
            if tower_id in direct_set:
                continue
            if not tower.get("on_grid_power", True):
                continue
            power_dep = tower.get("power_dependency", "")
            if power_dep in affected_set or tower_id in affected_set:
                logger.info(
                    f"[tick {self.tick}] Tower {tower_id} lost power "
                    f"(matched via affected_nodes)"
                )
                await self._tower_lose_grid_power(
                    tower_id, cascade_depth=event.cascade_depth + 1
                )

    # ══════════════════════════════════════════════════════════════════════════
    # TICK PROCESSING
    # ══════════════════════════════════════════════════════════════════════════

    async def _on_tick(self):
        """
        Per-tick physics. Order:
          1. Battery drain on off-grid towers
          2. Coverage radius degradation (FSPL-based scaling)
          3. Health updates + state-change events
        """
        await self._step_battery_drain()
        await self._step_coverage_degradation()
        await self._step_health_updates()
        self._generate_report_stub()

    # ── Battery drain ──────────────────────────────────────────────────────────
    async def _step_battery_drain(self):
        """
        Each off-grid tower drains its battery every tick:
          drain_kwh = power_consumption_kw × (TICK_DURATION_MINUTES / 60)

        Emits CELL_TOWER_BATTERY every tick while on battery so dashboard can
        show live battery bar.

        Emits CELL_TOWER_FAILED when battery_remaining_kwh reaches 0.
        """
        drain_kwh = None  # computed per tower (power varies by type)

        for nid, tower in self.nodes.items():
            if tower.get("on_grid_power", True):
                continue
            if tower.get("operational_status") == "failed":
                continue

            battery = tower["battery"]
            if not battery.get("on_battery", False):
                continue

            remaining = battery.get("remaining_kwh", 0.0)
            power_kw  = tower.get("power_consumption_kw", 4.0)
            drain     = power_kw * (TICK_DURATION_MINUTES / 60.0)
            new_remaining = max(0.0, round(remaining - drain, 4))
            battery["remaining_kwh"] = new_remaining

            capacity = max(battery.get("capacity_kwh", 1.0), 0.001)
            battery_fraction = new_remaining / capacity

            # update backup_hours remaining (for dashboard display)
            if power_kw > 0:
                battery["backup_hours"] = round(new_remaining / power_kw, 2)

            logger.debug(
                f"[tick {self.tick}] Tower {nid} battery: "
                f"{new_remaining:.2f}/{capacity:.2f} kWh "
                f"({battery_fraction*100:.0f}%)"
            )

            # emit battery status event every tick while on battery
            evt = cell_tower_battery(nid, self.tick,
                                     battery_remaining_kwh=new_remaining)
            await self._publish(evt)

            # battery depleted → tower fails
            if new_remaining <= 0.0:
                logger.info(
                    f"[tick {self.tick}] Tower {nid} ({tower.get('name')}) "
                    f"battery exhausted — tower going dark"
                )
                await self._on_tower_failed(nid, reason="battery_exhausted",
                                            cascade_depth=1)

    # ── Coverage degradation ───────────────────────────────────────────────────
    async def _step_coverage_degradation(self):
        """
        When a tower is on battery, coverage radius degrades proportionally.

        Below BATTERY_DEGRADE_COVERAGE_THRESHOLD (50%), transmit power is
        reduced to conserve battery. Coverage scales with sqrt of power ratio
        (Friis equation: received power ∝ 1/d², so max radius ∝ sqrt(P_tx)).

        coverage_radius = max_radius × max(COVERAGE_DEGRADE_FLOOR,
                                           sqrt(battery_fraction))
        """
        for nid, tower in self.nodes.items():
            if tower.get("on_grid_power", True):
                # restore full coverage if grid is back
                for p in tower.get("providers", []):
                    if p.get("active", True):
                        p["coverage_radius_m"] = p.get("max_coverage_radius_m",
                                                        p["coverage_radius_m"])
                continue

            battery  = tower["battery"]
            capacity = max(battery.get("capacity_kwh", 1.0), 0.001)
            remaining = battery.get("remaining_kwh", 0.0)
            battery_fraction = remaining / capacity

            if battery_fraction >= BATTERY_DEGRADE_COVERAGE_THRESHOLD:
                # full coverage while battery is healthy
                for p in tower.get("providers", []):
                    p["coverage_radius_m"] = p.get("max_coverage_radius_m",
                                                    p["coverage_radius_m"])
                continue

            # scale coverage down
            scale = max(
                COVERAGE_DEGRADE_FLOOR,
                math.sqrt(battery_fraction / BATTERY_DEGRADE_COVERAGE_THRESHOLD)
            )
            for p in tower.get("providers", []):
                if not p.get("active", True):
                    continue
                max_r = p.get("max_coverage_radius_m", p["coverage_radius_m"])
                p["coverage_radius_m"] = round(max_r * scale, 2)

    # ── Health updates + state-change events ──────────────────────────────────
    async def _step_health_updates(self):
        for nid, tower in self.nodes.items():
            old_health = tower.get("health", 1.0)
            new_health = tower["health"]

            if new_health <= HEALTH_FAIL_THRESHOLD and old_health > HEALTH_FAIL_THRESHOLD:
                tower["operational_status"] = "failed"
                evt = node_failed(Network.TELECOM, nid, self.tick)
                await self._publish(evt)

            elif new_health <= HEALTH_DEGRADE_THRESHOLD and old_health > HEALTH_DEGRADE_THRESHOLD:
                tower["operational_status"] = "degraded"
                evt = node_degraded(Network.TELECOM, nid, self.tick,
                                    severity=round(1.0 - new_health, 2))
                await self._publish(evt)

    # ══════════════════════════════════════════════════════════════════════════
    # TOWER POWER LOSS HANDLING
    # ══════════════════════════════════════════════════════════════════════════

    async def _tower_lose_grid_power(self, tower_id: str, cascade_depth: int):
        """
        Tower loses grid power — switch to battery backup.
        If no battery capacity (shouldn't happen with telecom.json data), fail immediately.
        """
        tower = self.nodes[tower_id]
        tower["on_grid_power"] = False
        battery = tower["battery"]

        if battery.get("remaining_kwh", 0.0) > 0:
            battery["on_battery"] = True
            logger.info(
                f"[tick {self.tick}] Tower {tower_id} ({tower.get('name')}) "
                f"lost grid — on battery "
                f"({battery['remaining_kwh']:.1f} kWh / "
                f"{battery.get('backup_hours', '?')}h remaining)"
            )
            # emit initial battery status
            evt = cell_tower_battery(
                tower_id, self.tick,
                battery_remaining_kwh=battery["remaining_kwh"]
            )
            await self._publish(evt)
        else:
            # no battery — fail immediately
            await self._on_tower_failed(tower_id, reason="no_battery",
                                        cascade_depth=cascade_depth)

    async def _on_tower_failed(self, tower_id: str, reason: str,
                                cascade_depth: int):
        """
        Tower is completely dark. Emit CELL_TOWER_FAILED and per-provider SIGNAL_LOSS.
        """
        tower = self.nodes[tower_id]
        if tower.get("operational_status") == "failed":
            return  # already processed

        tower["health"]             = 0.0
        tower["operational_status"] = "failed"
        tower["on_grid_power"]      = False
        tower["battery"]["on_battery"]      = False
        tower["battery"]["remaining_kwh"]   = 0.0

        # zero all provider coverage radii and mark inactive
        for p in tower.get("providers", []):
            p["coverage_radius_m"] = 0.0
            p["active"] = False

        # collect all providers' max coverage for event metadata
        max_coverage = max(
            (p.get("max_coverage_radius_m", 0) for p in tower.get("providers", [])),
            default=0.0
        )

        logger.info(
            f"[tick {self.tick}] Tower {tower_id} ({tower.get('name')}) FAILED "
            f"reason={reason} depth={cascade_depth}"
        )

        # CELL_TOWER_FAILED
        evt = cell_tower_failed(
            tower_id, self.tick,
            coverage_radius_m=max_coverage
        )
        await self._publish(evt)

        # NODE_FAILED (for orchestrator generic tracking)
        await self._publish(node_failed(
            Network.TELECOM, tower_id, self.tick,
            cascade_depth=cascade_depth,
            reason=reason
        ))

        # SIGNAL_LOSS per provider
        for p in tower.get("providers", []):
            signal_loss_evt = Event(
                EventType.SIGNAL_LOSS,
                Network.TELECOM,
                tower_id,
                severity=1.0,
                tick=self.tick,
                metadata={
                    "operator":         p.get("operator"),
                    "technology":       p.get("technology"),
                    "frequency_mhz":    p.get("frequency_mhz"),
                    "coverage_lost_m":  p.get("max_coverage_radius_m", 0),
                    "reason":           reason,
                    "cascade_depth":    cascade_depth,
                }
            )
            await self._publish(signal_loss_evt)

    # ══════════════════════════════════════════════════════════════════════════
    # FAIL / RESTORE
    # ══════════════════════════════════════════════════════════════════════════

    async def _fail_node(self, node_id: str, reason: str = "unknown",
                         cascade_depth: int = 0):
        """
        Hard-fail a tower. Entry point for both user-triggered and cascade failures.
        """
        if node_id not in self.nodes:
            logger.warning(f"_fail_node: unknown tower {node_id}")
            return

        tower = self.nodes[node_id]
        if tower.get("health", 1.0) <= HEALTH_FAIL_THRESHOLD:
            return  # already failed

        tower["on_grid_power"] = False
        tower["battery"]["on_battery"] = False
        tower["battery"]["remaining_kwh"] = 0.0

        await self._on_tower_failed(node_id, reason=reason,
                                    cascade_depth=cascade_depth)

    async def _restore_node(self, node_id: str):
        """
        Restore a failed tower (user-triggered).
        Restores grid power, resets battery to full capacity, re-enables all providers.
        """
        if node_id not in self.nodes:
            return

        tower = self.nodes[node_id]
        tower["health"]             = 1.0
        tower["operational_status"] = "normal"
        tower["on_grid_power"]      = True

        battery = tower["battery"]
        battery["on_battery"]    = False
        battery["remaining_kwh"] = battery.get("capacity_kwh", 0.0)
        # restore backup_hours from capacity / power
        power_kw = tower.get("power_consumption_kw", 4.0)
        if power_kw > 0:
            battery["backup_hours"] = round(
                battery["remaining_kwh"] / power_kw, 2
            )

        # restore all provider coverage radii and mark active
        for p in tower.get("providers", []):
            p["coverage_radius_m"] = p.get("max_coverage_radius_m",
                                            p.get("coverage_radius_m", 0))
            p["active"] = True

        evt = node_recovered(Network.TELECOM, node_id, self.tick,
                             reason="user_restore")
        await self._publish(evt)
        logger.info(f"[tick {self.tick}] Tower {node_id} restored")

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLISH HELPER
    # ══════════════════════════════════════════════════════════════════════════

    async def _publish(self, event: Event):
        event.tick = self.tick
        await self.bus.publish(event)
        self.event_log.append(event.to_dict())

    # ══════════════════════════════════════════════════════════════════════════
    # REPORT STUB
    # ══════════════════════════════════════════════════════════════════════════

    def _generate_report_stub(self):
        """
        LLM situation report stub.
        Replace the body of this method with a real API call when ready:

            response = await fetch("https://api.anthropic.com/v1/messages", ...)
            self.last_report = response["content"][0]["text"]

        The report should summarise:
          - How many towers are currently on battery / failed
          - Which operators/technologies have coverage gaps
          - Estimated % of area without coverage
          - Recommended actions (restore priority)
        """
        failed   = self.get_failed_nodes()
        on_batt  = [nid for nid, n in self.nodes.items()
                    if n["battery"].get("on_battery", False)]
        degraded = self.get_degraded_nodes()
        self.last_report = (
            f"[tick {self.tick}] Telecom: {len(failed)} towers failed, "
            f"{len(on_batt)} on battery, {len(degraded)} degraded. "
            f"LLM report stub — integrate API here."
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC API (for orchestrator / dashboard)
    # ══════════════════════════════════════════════════════════════════════════

    def get_node_state(self, node_id: str) -> Optional[Dict]:
        return self.nodes.get(node_id)

    def get_all_states(self) -> Dict[str, Dict]:
        return {
            nid: {
                "health":             n.get("health", 1.0),
                "operational_status": n.get("operational_status", "normal"),
                "on_grid_power":      n.get("on_grid_power", True),
                "tower_type":         n.get("tower_type", "unknown"),
                "name":               n.get("name", nid),
                "flood_risk":         n.get("flood_risk", False),
                "battery_remaining":  n["battery"].get("remaining_kwh", 0.0),
                "battery_capacity":   n["battery"].get("capacity_kwh", 0.0),
                "battery_fraction":   (
                    n["battery"].get("remaining_kwh", 0.0) /
                    max(n["battery"].get("capacity_kwh", 1.0), 0.001)
                ),
                "on_battery":         n["battery"].get("on_battery", False),
                "providers":          [
                    {
                        "operator":          p.get("operator"),
                        "technology":        p.get("technology"),
                        "coverage_radius_m": p.get("coverage_radius_m"),
                        "active":            p.get("active", True),
                    }
                    for p in n.get("providers", [])
                ],
                "power_dependency":   n.get("power_dependency"),
            }
            for nid, n in self.nodes.items()
        }

    def get_failed_nodes(self) -> List[str]:
        return [nid for nid, n in self.nodes.items()
                if n.get("health", 1.0) <= HEALTH_FAIL_THRESHOLD]

    def get_degraded_nodes(self) -> List[str]:
        return [nid for nid, n in self.nodes.items()
                if HEALTH_FAIL_THRESHOLD < n.get("health", 1.0) <= HEALTH_DEGRADE_THRESHOLD]

    def get_towers_on_battery(self) -> List[str]:
        return [nid for nid, n in self.nodes.items()
                if n["battery"].get("on_battery", False)]

    def summary(self) -> Dict:
        failed   = self.get_failed_nodes()
        on_batt  = self.get_towers_on_battery()
        degraded = self.get_degraded_nodes()

        # count signal losses by operator
        signal_losses = {}
        for n in self.nodes.values():
            if n.get("operational_status") == "failed":
                for p in n.get("providers", []):
                    op = p.get("operator", "unknown")
                    signal_losses[op] = signal_losses.get(op, 0) + 1

        return {
            "network":          "telecom",
            "tick":             self.tick,
            "total_towers":     len(self.nodes),
            "failed":           len(failed),
            "on_battery":       len(on_batt),
            "degraded":         len(degraded),
            "failed_ids":       failed,
            "on_battery_ids":   on_batt,
            "signal_losses_by_operator": signal_losses,
            "report":           self.last_report,
        }

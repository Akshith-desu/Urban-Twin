"""
power_agent.py  —  Phase 3
Power sector agent for the Multi-Agent Urban Digital Twin.

Owns the power infrastructure graph (loaded from graphs/power.json).
Listens to the event bus for user-initiated failures and cross-network events.
Applies DC power flow, thermal overload, and relay-trip cascade logic every tick.
Publishes state-change events back to the bus.

Tick duration: 5 real minutes per tick (defined in event_schema.py).

Cascade logic (mirrors power.py bottom comments):
  Step 1 — Substation fails → all its transformers reroute to backup substation
  Step 2 — Backup substation recomputes load_fraction
  Step 3 — Each feeder line checks IEC 60255-151 relay trip time
  Step 4 — Tripped feeder → transformer loses supply → buildings go dark
  Step 5 — Joule heating on feeder lines (parallel with Step 3)
  Step 6 — Line fails from thermal overload if relay hasn't tripped

Cross-network outputs:
  FEEDER_LINE_DROPPED  → water agent (pump loses power)
  FEEDER_LINE_DROPPED  → telecom agent (cell tower loses power)
  FEEDER_LINE_DROPPED  → road agent (traffic signal loses power)
  SUBSTATION_FAILED    → orchestrator
"""

import json
import math
import asyncio
import logging
from typing import Dict, List, Optional, Any

from event_schema import (
    Event, EventType, Network, TICK_DURATION_MINUTES,
    node_failed, node_degraded, node_recovered,
    cascade_triggered, substation_failed, feeder_line_dropped,
    user_fail_node, user_restore_node, sim_tick,
)
from event_bus import EventBus

logger = logging.getLogger(__name__)


# ── cascade / physics constants ────────────────────────────────────────────────
IEC_STANDARD_INVERSE_K   = 0.14    # IEC 60255-151 standard inverse
IEC_STANDARD_INVERSE_A   = 0.02    # exponent for standard inverse curve
OVERLOAD_HEALTH_PENALTY  = 0.08    # health drop per tick while overloaded
THERMAL_AMBIENT_C        = 35.0    # ambient temperature (Bengaluru)
HEALTH_FAIL_THRESHOLD    = 0.10    # health below this = failed
HEALTH_DEGRADE_THRESHOLD = 0.50    # health below this = degraded event emitted
LOAD_SPIKE_THRESHOLD     = 0.90    # load_fraction above this emits LOAD_SPIKE


class PowerAgent:
    """
    Autonomous power sector agent.

    State: in-memory graph of all power nodes and edges loaded from power.json.
    Each node and edge dict is mutated in place each tick — no copies.

    Subscriptions:
        USER_FAIL_NODE    — user failed a power node from frontend
        USER_RESTORE_NODE — user restored a power node from frontend
        USER_FLOOD_ZONE   — flood marks power nodes at risk
        FLOOD_NODE        — flood event from scenario engine
        SIMULATION_TICK   — advance one tick

    Publishes:
        SUBSTATION_FAILED, FEEDER_LINE_DROPPED, TRANSFORMER_REROUTED,
        TRANSFORMER_OVERLOAD, LOAD_SPIKE, NODE_FAILED, NODE_DEGRADED,
        NODE_RECOVERED, CASCADE_TRIGGERED
    """

    SUBSCRIBED_EVENTS = [
        EventType.USER_FAIL_NODE,
        EventType.USER_RESTORE_NODE,
        EventType.USER_FLOOD_ZONE,
        EventType.FLOOD_NODE,
        EventType.FLOOD_CLEARED,
        EventType.SIMULATION_TICK,
    ]

    def __init__(self, bus: EventBus,
                 power_json_path: str = "graphs/power.json"):
        self.bus  = bus
        self.tick = 0
        self.name = "power_agent"

        # ── load graph from JSON ───────────────────────────────────────────────
        with open(power_json_path, "r") as f:
            raw = json.load(f)

        # nodes keyed by node_id
        self.nodes: Dict[str, Dict] = {}
        # ── name ↔ id resolution ───────────────────────────────────────────────
        # power.json edges use node NAMES ("Transformer 1", "Substation 1")
        # but self.nodes is keyed by node_id ("0", "35"). We need to resolve.
        self._name_to_id: Dict[str, str] = {}   # "Substation 1" → "35"
        self._id_to_name: Dict[str, str] = {}   # "35" → "Substation 1"

        for n in raw["nodes"]:
            nid = str(n["node_id"])
            node = dict(n)

            # augment with runtime state not stored in JSON
            node.setdefault("health",             1.0)
            node.setdefault("operational_status", "normal")
            node.setdefault("load_fraction",      node.get("load_fraction", 0.5))
            node.setdefault("ticks_overloaded",   0)
            node.setdefault("on_grid_power",      True)
            node.setdefault("flood_risk",         False)
            # track which buildings / downstream nodes this node feeds
            node["fed_nodes"] = []    # populated during edge load below

            self.nodes[nid] = node

            # build bidirectional name ↔ id map
            node_name = str(node.get("name", ""))
            if node_name:
                self._name_to_id[node_name] = nid
                self._id_to_name[nid] = node_name

        # edges keyed by edge_id, also indexed for fast lookup
        self.edges: Dict[int, Dict] = {}
        # transformer node_id → list of edge_ids for its primary supply edges
        self._primary_edges:  Dict[str, List[int]] = {}
        # substation node_id → list of transformer node_ids it supplies
        self._sub_to_transformers: Dict[str, List[str]] = {}
        # building name → transformer node_id it connects to
        self._building_to_transformer: Dict[str, str] = {}

        for e in raw["edges"]:
            eid  = int(e["edge_id"])
            edge = dict(e)
            edge.setdefault("health",                  1.0)
            edge.setdefault("blocked",                 False)
            edge.setdefault("current_flow_mw",         0.0)
            edge.setdefault("current_temperature_c",   THERMAL_AMBIENT_C)
            edge.setdefault("time_overloaded_seconds",  0.0)
            self.edges[eid] = edge

            # edge from/to are NODE NAMES — resolve to node_ids
            fnode_name = str(edge.get("from", ""))
            tnode_name = str(edge.get("to",   ""))
            fnode_id   = self._name_to_id.get(fnode_name, fnode_name)
            tnode_id   = self._name_to_id.get(tnode_name, tnode_name)

            # store resolved ids back on the edge for later use
            edge["from_id"] = fnode_id
            edge["to_id"]   = tnode_id

            if edge.get("edge_type") == "primary_supply":
                # edges go FROM transformer TO substation
                # fnode = transformer name, tnode = substation name
                # We want substation_id → [transformer_ids]
                self._primary_edges.setdefault(fnode_id, []).append(eid)
                self._sub_to_transformers.setdefault(tnode_id, []).append(fnode_id)
                # add transformer to substation's fed_nodes
                if tnode_id in self.nodes:
                    self.nodes[tnode_id]["fed_nodes"].append(fnode_id)

            elif edge.get("edge_type") == "building_service_drop":
                self._building_to_transformer[fnode_name] = tnode_id

        logger.info(
            f"  Name→ID map: {len(self._name_to_id)} entries. "
            f"Sub→Transformers: { {self._id_to_name.get(k,k): len(v) for k,v in self._sub_to_transformers.items()} }"
        )

        # event queues (filled by bus.subscribe)
        self._queues: Dict[EventType, asyncio.Queue] = {}

        # published event log for orchestrator / dashboard
        self.event_log: List[Dict] = []

        # LLM report stub — replace with real API call when ready
        self.last_report: str = ""

        logger.info(
            f"PowerAgent loaded: {len(self.nodes)} nodes, "
            f"{len(self.edges)} edges"
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
        """Process all pending events from subscribed queues."""
        for et, q in self._queues.items():
            while not q.empty():
                event: Event = q.get_nowait()
                await self._handle(event)

    # ══════════════════════════════════════════════════════════════════════════
    # EVENT HANDLER
    # ══════════════════════════════════════════════════════════════════════════

    async def _handle(self, event: Event):
        """Route incoming event to the right handler."""

        if event.event_type == EventType.SIMULATION_TICK:
            self.tick = event.tick
            await self._on_tick()

        elif event.event_type == EventType.USER_FAIL_NODE:
            if event.source_network == Network.POWER:
                await self._fail_node(event.node_id, reason="user_triggered",
                                      cascade_depth=0)

        elif event.event_type == EventType.USER_RESTORE_NODE:
            if event.source_network == Network.POWER:
                await self._restore_node(event.node_id)

        elif event.event_type in (EventType.FLOOD_NODE, EventType.USER_FLOOD_ZONE):
            node_id = event.node_id
            if node_id in self.nodes:
                self.nodes[node_id]["flood_risk"] = True
                # ground-level power infrastructure is vulnerable to flooding
                ntype = self.nodes[node_id].get("power", "")
                if ntype in ("substation", "transformer"):
                    # probabilistic failure — substations more vulnerable
                    import random
                    fail_prob = 0.7 if ntype == "substation" else 0.4
                    if random.random() < fail_prob:
                        await self._fail_node(node_id, reason="flood",
                                              cascade_depth=0)

        elif event.event_type == EventType.FLOOD_CLEARED:
            if event.node_id in self.nodes:
                self.nodes[event.node_id]["flood_risk"] = False

    # ══════════════════════════════════════════════════════════════════════════
    # TICK PROCESSING
    # ══════════════════════════════════════════════════════════════════════════

    async def _on_tick(self):
        """
        Per-tick cascade physics. Called every SIMULATION_TICK.
        Order matters:
          1. Thermal heating on all active feeder lines
          2. Relay trip check on overloaded feeders
          3. Load rebalance after any new failures
          4. Health updates on all nodes
          5. Emit state-change events
        """
        await self._step_thermal()
        await self._step_relay_trips()
        await self._step_health_updates()
        self._generate_report_stub()

    async def _step_thermal(self):
        """
        Joule heating on feeder lines (power.py cascade Step 5).
        dT/dt = (P_heat - cooling × (T - T_ambient)) / thermal_time_constant
        T_new = T_current + dT/dt × tick_duration_minutes
        """
        for eid, edge in self.edges.items():
            if edge.get("blocked") or edge.get("edge_type") not in (
                    "primary_supply", "backup_supply"):
                continue

            I_flow  = edge.get("current_flow_mw", 0.0)
            R_ohm   = edge.get("resistance_ohm", 0.1)
            T_cur   = edge.get("current_temperature_c", THERMAL_AMBIENT_C)
            T_lim   = edge.get("thermal_limit_c", 90.0)
            tau_min = edge.get("thermal_time_constant_min", 15.0)
            cooling = edge.get("cooling_capacity", 0.03)

            if I_flow <= 0:
                # cool toward ambient
                T_new = T_cur + (THERMAL_AMBIENT_C - T_cur) / tau_min * TICK_DURATION_MINUTES
            else:
                P_heat = (I_flow ** 2) * R_ohm
                dT_dt  = (P_heat - cooling * (T_cur - THERMAL_AMBIENT_C)) / tau_min
                T_new  = T_cur + dT_dt * TICK_DURATION_MINUTES

            edge["current_temperature_c"] = round(T_new, 2)

            if T_new > T_lim:
                edge["time_overloaded_seconds"] += TICK_DURATION_MINUTES * 60

    async def _step_relay_trips(self):
        """
        IEC 60255-151 standard inverse relay trip check (power.py cascade Step 3).
        t_trip = TMS × (K / ((I/I_rated)^A - 1))
        If time_overloaded >= t_trip → feeder trips → transformer loses supply.
        """
        for eid, edge in self.edges.items():
            if edge.get("blocked") or edge.get("edge_type") not in (
                    "primary_supply", "backup_supply"):
                continue

            rated  = edge.get("rated_capacity_mw", 5.0)
            I_flow = edge.get("current_flow_mw",   0.0)
            TMS    = edge.get("relay_TMS",          0.2)
            t_over = edge.get("time_overloaded_seconds", 0.0)

            if rated <= 0 or I_flow <= rated:
                edge["time_overloaded_seconds"] = max(0, t_over - 30)
                continue

            ratio = I_flow / rated
            if ratio <= 1.0:
                continue

            try:
                t_trip = TMS * (IEC_STANDARD_INVERSE_K / (ratio ** IEC_STANDARD_INVERSE_A - 1))
            except ZeroDivisionError:
                t_trip = float("inf")

            if t_over >= t_trip:
                # relay trips — feeder line drops
                edge["blocked"] = True
                edge["health"]  = 0.0
                # edges: from=transformer_name, to=substation_name
                # the transformer is the FROM node
                from_id = edge.get("from_id", "")
                logger.info(
                    f"[tick {self.tick}] Feeder {eid} tripped "
                    f"(ratio={ratio:.2f}, t_trip={t_trip:.0f}s)"
                )
                # transformer loses primary supply → try backup
                if from_id in self.nodes:
                    await self._handle_transformer_supply_loss(
                        from_id, failed_edge_id=eid, cascade_depth=1
                    )

            elif ratio > 1.0:
                # accumulate overload time
                edge["time_overloaded_seconds"] += TICK_DURATION_MINUTES * 60
                # update current flow on feeder's transformer
                from_id = edge.get("from_id", "")
                if from_id in self.nodes:
                    self.nodes[from_id]["load_fraction"] = min(ratio, 3.0)

    async def _step_health_updates(self):
        """
        Update node health and emit degraded/failed events where needed.
        """
        for nid, node in self.nodes.items():
            old_health = node.get("health", 1.0)
            ntype      = node.get("power", node.get("node_type", ""))

            # nodes that are overloaded lose health each tick
            if node.get("load_fraction", 0) > LOAD_SPIKE_THRESHOLD:
                node["ticks_overloaded"] = node.get("ticks_overloaded", 0) + 1
                if node["ticks_overloaded"] > 2:
                    node["health"] = max(0.0, old_health - OVERLOAD_HEALTH_PENALTY)
            else:
                node["ticks_overloaded"] = 0

            new_health = node["health"]

            # emit events on threshold crossings
            if new_health <= HEALTH_FAIL_THRESHOLD and old_health > HEALTH_FAIL_THRESHOLD:
                node["operational_status"] = "failed"
                evt = node_failed(Network.POWER, nid, self.tick)
                await self._publish(evt)
                if ntype == "substation":
                    await self._cascade_substation_failure(nid, cascade_depth=1)

            elif new_health <= HEALTH_DEGRADE_THRESHOLD and old_health > HEALTH_DEGRADE_THRESHOLD:
                node["operational_status"] = "degraded"
                evt = node_degraded(Network.POWER, nid, self.tick,
                                    severity=round(1.0 - new_health, 2))
                await self._publish(evt)

            # emit LOAD_SPIKE for overloaded substations
            if (ntype == "substation"
                    and node.get("load_fraction", 0) > LOAD_SPIKE_THRESHOLD):
                evt = Event(
                    EventType.LOAD_SPIKE, Network.POWER, nid,
                    severity=round(node["load_fraction"] - 1.0, 3),
                    tick=self.tick,
                    metadata={"load_fraction": node["load_fraction"]}
                )
                await self._publish(evt)

    # ══════════════════════════════════════════════════════════════════════════
    # CASCADE LOGIC
    # ══════════════════════════════════════════════════════════════════════════

    async def _fail_node(self, node_id: str, reason: str = "unknown",
                         cascade_depth: int = 0):
        """
        Hard-fail a node. Entry point for both user-triggered and cascade failures.
        """
        if node_id not in self.nodes:
            logger.warning(f"_fail_node: unknown node {node_id}")
            return

        node = self.nodes[node_id]
        if node.get("health", 1.0) <= HEALTH_FAIL_THRESHOLD:
            return  # already failed

        node["health"]             = 0.0
        node["operational_status"] = "failed"
        node["on_grid_power"]      = False

        ntype = node.get("power", node.get("node_type", ""))
        logger.info(
            f"[tick {self.tick}] FAIL {ntype} {node_id} reason={reason} "
            f"depth={cascade_depth}"
        )

        evt = node_failed(Network.POWER, node_id, self.tick,
                          cascade_depth=cascade_depth, reason=reason)
        await self._publish(evt)

        if ntype == "substation":
            await self._cascade_substation_failure(node_id, cascade_depth)
        elif ntype == "transformer":
            await self._cascade_transformer_failure(node_id, cascade_depth)

    async def _cascade_substation_failure(self, sub_id: str, cascade_depth: int):
        """
        Substation fails (power.py cascade Step 1+2).
        """
        sub_name = self._id_to_name.get(sub_id, sub_id)
        logger.info(f"\n{'='*60}\nPOWER CASCADE START: Substation '{sub_name}' (ID: {sub_id}) failed!\n{'='*60}")

        # 1. Transformers from original edge mapping
        transformers_from_edges = set(self._sub_to_transformers.get(sub_id, []))
        logger.info(f"  - Found {len(transformers_from_edges)} transformers from primary supply edges")

        # 2. Transformers rerouted here (substation_supplying changed at runtime)
        transformers_rerouted = set(
            nid for nid, n in self.nodes.items()
            if (n.get("power") == "transformer"
                and n.get("substation_supplying") == sub_name
                and nid not in transformers_from_edges
                and n.get("operational_status") != "failed")
        )

        transformers = list(transformers_from_edges | transformers_rerouted)

        if not transformers:
            logger.warning(
                f"  No transformers found for substation {sub_id} "
                f"(name={sub_name})"
            )
            return

        if transformers_rerouted:
            logger.info(
                f"[tick {self.tick}] Substation {sub_id} ({sub_name}) cascade: "
                f"{len(transformers)} transformers affected "
                f"({len(transformers_from_edges)} original + "
                f"{len(transformers_rerouted)} rerouted here)"
            )
        else:
            logger.info(
                f"[tick {self.tick}] Substation {sub_id} ({sub_name}) cascade: "
                f"{len(transformers)} transformers affected"
            )

        evt = substation_failed(sub_id, self.tick,
                                affected_transformers=transformers,
                                reason="health_zero")
        await self._publish(evt)

        rerouted = []
        for t_id in transformers:
            if t_id not in self.nodes:
                continue
            t_node  = self.nodes[t_id]
            if t_node.get("operational_status") == "failed":
                continue  # already dark

            # backup substation is stored as a NAME — resolve to node_id
            backup_name = t_node.get("second_nearest_substation_supplying")
            backup_id   = self._name_to_id.get(str(backup_name), "") if backup_name else ""

            # verify backup is actually alive (not just exists)
            backup_alive = (
                backup_id
                and backup_id in self.nodes
                and backup_id != sub_id
                and self.nodes[backup_id].get("health", 1.0) > HEALTH_FAIL_THRESHOLD
                and self.nodes[backup_id].get("operational_status") != "failed"
            )

            if backup_alive:
                old_primary_name = t_node.get("substation_supplying")
                t_node["substation_supplying"] = backup_name
                rerouted.append(t_id)
                logger.info(
                    f"  Transformer {t_id} ({self._id_to_name.get(t_id, '?')}): "
                    f"rerouted {old_primary_name} -> {backup_name}"
                )
                # increase backup substation load
                backup_node = self.nodes[backup_id]
                extra_load  = t_node.get("rated_capacity_mw", 0.1) / max(
                    backup_node.get("rated_capacity_mw", 1.0), 0.001
                )
                backup_node["load_fraction"] = min(
                    backup_node.get("load_fraction", 0.5) + extra_load, 3.0
                )
                # emit reroute event
                await self._publish(Event(
                    EventType.TRANSFORMER_REROUTED, Network.POWER, t_id,
                    severity=0.3, tick=self.tick,
                    metadata={"from_sub": sub_id, "to_sub": backup_id,
                               "from_sub_name": self._id_to_name.get(sub_id, sub_id),
                               "to_sub_name": backup_name,
                               "extra_load": round(extra_load, 4)}
                ))
            else:
                # no viable backup — transformer loses supply entirely
                reason = "backup_also_failed" if (backup_id and backup_id in self.nodes
                    and self.nodes[backup_id].get("operational_status") == "failed") else "no_backup"
                logger.info(
                    f"  Transformer {t_id} ({self._id_to_name.get(t_id, '?')}): "
                    f"{reason} -- goes dark"
                )
                await self._cascade_transformer_failure(t_id, cascade_depth + 1)

        # update feeder flows on all backup substations that absorbed load
        recomputed_subs = set()
        for t_id in rerouted:
            t_node = self.nodes[t_id]
            backup_name = t_node.get("substation_supplying")
            backup_id = self._name_to_id.get(str(backup_name), "") if backup_name else ""
            if backup_id and backup_id not in recomputed_subs:
                recomputed_subs.add(backup_id)
                await self._recompute_feeder_flows(backup_id)

    async def _handle_transformer_supply_loss(self, transformer_id: str,
                                               failed_edge_id: int,
                                               cascade_depth: int):
        """
        Called when a primary feeder line to a transformer trips.
        Tries to fall back to backup_supply edge. If none available, fails transformer.
        """
        if transformer_id not in self.nodes:
            return

        t_node = self.nodes[transformer_id]
        backup_sub_name = t_node.get("second_nearest_substation_supplying")
        backup_sub_id   = self._name_to_id.get(str(backup_sub_name), "") if backup_sub_name else ""

        # check if there is a live backup_supply edge
        # edges use names, so match by transformer name
        t_name = self._id_to_name.get(transformer_id, transformer_id)
        backup_edges = [
            e for e in self.edges.values()
            if (str(e.get("from")) == t_name
                and e.get("edge_type") == "backup_supply"
                and not e.get("blocked"))
        ]

        if backup_edges and backup_sub_id and backup_sub_id in self.nodes:
            # switch to backup
            t_node["substation_supplying"] = backup_sub_name
            await self._publish(Event(
                EventType.TRANSFORMER_REROUTED, Network.POWER, transformer_id,
                severity=0.3, tick=self.tick,
                metadata={"reason": "primary_feeder_tripped",
                           "backup_sub": backup_sub_id,
                           "backup_sub_name": backup_sub_name}
            ))
        else:
            # no backup — transformer is dark
            await self._cascade_transformer_failure(transformer_id, cascade_depth)

    async def _cascade_transformer_failure(self, transformer_id: str,
                                            cascade_depth: int):
        """
        Transformer loses supply (power.py cascade Step 4).
        All buildings connected to it lose power.
        Emits FEEDER_LINE_DROPPED with affected building list.
        Other agents subscribe to this to know their nodes lost power.
        """
        if transformer_id not in self.nodes:
            return

        node = self.nodes[transformer_id]
        if node.get("health", 1.0) > HEALTH_FAIL_THRESHOLD:
            node["health"]             = 0.0
            node["operational_status"] = "failed"
            node["on_grid_power"]      = False

        # find all buildings served by this transformer (mapped by node_id)
        affected_buildings = [
            bldg for bldg, t_id in self._building_to_transformer.items()
            if t_id == transformer_id
        ]

        t_name = self._id_to_name.get(transformer_id, transformer_id)
        logger.info(
            f"[tick {self.tick}] Transformer {transformer_id} ({t_name}) dark — "
            f"{len(affected_buildings)} buildings lose power"
        )

        evt = feeder_line_dropped(
            Network.POWER, transformer_id, self.tick,
            affected_nodes=affected_buildings,
            cascade_depth=cascade_depth,
            transformer_id=transformer_id
        )
        evt.metadata["transformer_name"] = t_name
        await self._publish(evt)

        # also emit NODE_FAILED for each affected building
        # (water/telecom/road agents use this to mark their own dependency loss)
        for bldg_id in affected_buildings:
            await self._publish(node_failed(
                Network.POWER, bldg_id, self.tick,
                cascade_depth=cascade_depth + 1,
                reason="transformer_dark",
                transformer_id=transformer_id
            ))

    async def _restore_node(self, node_id: str):
        """
        Restore a failed/degraded node (user-triggered or recovery logic).
        """
        if node_id not in self.nodes:
            return
        node = self.nodes[node_id]
        node["health"]             = 1.0
        node["operational_status"] = "normal"
        node["on_grid_power"]      = True
        node["ticks_overloaded"]   = 0
        node["load_fraction"]      = 0.5

        # restore feeder edges from this node (use resolved from_id)
        for edge in self.edges.values():
            if edge.get("from_id") == node_id:
                edge["blocked"] = False
                edge["health"]  = 1.0
                edge["time_overloaded_seconds"] = 0.0
                edge["current_temperature_c"]   = THERMAL_AMBIENT_C

        evt = node_recovered(Network.POWER, node_id, self.tick,
                             reason="user_restore")
        await self._publish(evt)
        logger.info(f"[tick {self.tick}] Node {node_id} restored")

    async def _recompute_feeder_flows(self, substation_id: str):
        """
        DC Power Flow approximation (P ≈ (θ_i − θ_j) / X_ij).
        Simplified: distribute substation's total load equally across
        its active transformers and update current_flow_mw on feeder edges.
        Full DC power flow requires bus admittance matrix — that is Phase 6
        territory. This gives correct cascade behaviour without the matrix solve.
        """
        if substation_id not in self.nodes:
            return
        sub = self.nodes[substation_id]
        sub_name = self._id_to_name.get(substation_id, substation_id)

        # active transformers currently supplied by this substation
        # substation_supplying is stored as a NAME, so compare against sub_name
        active_transformers = [
            t_id for t_id in self._sub_to_transformers.get(substation_id, [])
            if (t_id in self.nodes
                and self.nodes[t_id].get("substation_supplying") == sub_name
                and self.nodes[t_id].get("operational_status") != "failed")
        ]

        if not active_transformers:
            sub["load_fraction"] = 0.0
            return

        sub_capacity = max(sub.get("rated_capacity_mw", 10.0), 0.001)
        total_demand = sum(
            self.nodes[t].get("rated_capacity_mw", 0.1)
            for t in active_transformers
        )
        sub["load_fraction"] = round(total_demand / sub_capacity, 3)

        # distribute flow across feeder edges
        # edges use names: edge["to"] = substation name, edge["from"] = transformer name
        per_transformer_flow = total_demand / len(active_transformers)
        for eid, edge in self.edges.items():
            if (not edge.get("blocked")
                    and edge.get("edge_type") in ("primary_supply", "backup_supply")
                    and edge.get("to_id") == substation_id):
                edge["current_flow_mw"] = round(per_transformer_flow, 4)

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
          - How many substations/transformers are currently failed/degraded
          - Which critical facilities (hospitals etc.) have lost power
          - Current worst-case cascade depth
          - Recommended actions
        """
        failed    = [n for n, d in self.nodes.items() if d.get("health", 1) <= 0.1]
        degraded  = [n for n, d in self.nodes.items()
                     if 0.1 < d.get("health", 1) <= 0.5]
        self.last_report = (
            f"[tick {self.tick}] Power: {len(failed)} failed, "
            f"{len(degraded)} degraded nodes. "
            f"LLM report stub — integrate API here."
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC API (for orchestrator / dashboard)
    # ══════════════════════════════════════════════════════════════════════════

    def get_node_state(self, node_id: str) -> Optional[Dict]:
        """Return current state dict for a single node."""
        return self.nodes.get(node_id)

    def get_all_states(self) -> Dict[str, Dict]:
        """Return snapshot of all node states for dashboard."""
        return {
            nid: {
                "health":             n.get("health", 1.0),
                "operational_status": n.get("operational_status", "normal"),
                "load_fraction":      n.get("load_fraction", 0.0),
                "on_grid_power":      n.get("on_grid_power", True),
                "flood_risk":         n.get("flood_risk", False),
                "node_type":          n.get("power", n.get("node_type", "unknown")),
                "name":               n.get("name", nid),
            }
            for nid, n in self.nodes.items()
        }

    def get_failed_nodes(self) -> List[str]:
        return [nid for nid, n in self.nodes.items()
                if n.get("health", 1.0) <= HEALTH_FAIL_THRESHOLD]

    def get_degraded_nodes(self) -> List[str]:
        return [nid for nid, n in self.nodes.items()
                if HEALTH_FAIL_THRESHOLD < n.get("health", 1.0) <= HEALTH_DEGRADE_THRESHOLD]

    def summary(self) -> Dict:
        failed   = self.get_failed_nodes()
        degraded = self.get_degraded_nodes()
        return {
            "network":        "power",
            "tick":           self.tick,
            "total_nodes":    len(self.nodes),
            "failed":         len(failed),
            "degraded":       len(degraded),
            "failed_ids":     failed,
            "report":         self.last_report,
        }
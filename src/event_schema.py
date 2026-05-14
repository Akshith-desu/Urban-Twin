

"""
event_schema.py
Typed event definitions for the entire simulation.
Every agent uses these — never create raw dicts for events.

Tick duration: 5 minutes real time per simulation tick.
This means:
  - Equation 1 pump decay: t = tick * 5 minutes
  - At tick 2  (10 min): pump at 43% pressure
  - At tick 6  (30 min): pump at 8%  pressure
  - At tick 10 (50 min): pump effectively dead
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum
import time
import uuid

# Real minutes represented by one simulation tick
TICK_DURATION_MINUTES = 5.0


class EventType(str, Enum):
    # ── core state changes ─────────────────────────────────────────────────────
    NODE_FAILED      = "NODE_FAILED"
    NODE_DEGRADED    = "NODE_DEGRADED"
    NODE_RECOVERED   = "NODE_RECOVERED"

    # ── cascade trigger ────────────────────────────────────────────────────────
    CASCADE_TRIGGERED = "CASCADE_TRIGGERED"

    # ── power network specific ─────────────────────────────────────────────────
    SUBSTATION_FAILED   = "SUBSTATION_FAILED"       # substation health = 0
    FEEDER_LINE_DROPPED = "FEEDER_LINE_DROPPED"     # feeder edge blocked=True
    TRANSFORMER_REROUTED = "TRANSFORMER_REROUTED"   # switched to backup substation
    TRANSFORMER_OVERLOAD = "TRANSFORMER_OVERLOAD"   # load_fraction > 1.0
    LOAD_SPIKE          = "LOAD_SPIKE"              # surviving substation absorbs extra load

    # ── water network specific ─────────────────────────────────────────────────
    PUMP_STATION_FAIL  = "PUMP_STATION_FAIL"        # pump loses power or health=0
    PUMP_ON_BACKUP     = "PUMP_ON_BACKUP"           # pump switched to backup gen
    PRESSURE_DROP      = "PRESSURE_DROP"            # junction pressure below threshold
    PIPE_BURST         = "PIPE_BURST"               # Equation 6 fired at junction
    TOWER_DRAINING     = "TOWER_DRAINING"           # tower fill source offline
    TOWER_EMPTY        = "TOWER_EMPTY"              # water_level = 0

    # ── telecom network specific ───────────────────────────────────────────────
    CELL_TOWER_BATTERY  = "CELL_TOWER_BATTERY"      # tower switched to battery
    CELL_TOWER_FAILED   = "CELL_TOWER_FAILED"       # battery depleted, tower down
    SIGNAL_LOSS         = "SIGNAL_LOSS"             # coverage hole created
    FIBER_CUT           = "FIBER_CUT"               # wired inter-tower link damaged

    # ── road network specific ──────────────────────────────────────────────────
    TRAFFIC_SIGNAL_UPS    = "TRAFFIC_SIGNAL_UPS"    # signal on UPS battery
    TRAFFIC_SIGNAL_FAILED = "TRAFFIC_SIGNAL_FAILED" # UPS depleted, signal dark
    ROUTE_BLOCKED         = "ROUTE_BLOCKED"         # road node impassable
    CONGESTION_SPIKE      = "CONGESTION_SPIKE"      # BPR travel time exceeded threshold

    # ── flood scenario ─────────────────────────────────────────────────────────
    FLOOD_NODE     = "FLOOD_NODE"           # node entered flood zone
    FLOOD_CLEARED  = "FLOOD_CLEARED"        # flood receded from node

    # ── user-initiated triggers (frontend → simulation) ────────────────────────
    USER_FAIL_NODE    = "USER_FAIL_NODE"    # user clicked "fail this node"
    USER_RESTORE_NODE = "USER_RESTORE_NODE" # user clicked "restore this node"
    USER_FLOOD_ZONE   = "USER_FLOOD_ZONE"   # user activated flood polygon

    # ── orchestration ──────────────────────────────────────────────────────────
    SIMULATION_TICK  = "SIMULATION_TICK"
    SIMULATION_END   = "SIMULATION_END"
    SIMULATION_START = "SIMULATION_START"
    AGENT_REPORT     = "AGENT_REPORT"       # situation report from agent (LLM stub)


class Network(str, Enum):
    POWER   = "power"
    WATER   = "water"
    TELECOM = "telecom"
    SYSTEM  = "system"   # orchestrator / simulation engine


@dataclass
class Event:
    """
    Single simulation event. All inter-agent communication uses this schema.

    Fields:
        event_id        - unique UUID (8 chars), auto-generated
        timestamp       - unix timestamp, auto-generated
        tick            - simulation tick number
        event_type      - one of EventType enum values
        source_network  - which network emitted this event
        node_id         - primary node affected
        severity        - 0.0 (no impact) to 1.0 (complete failure)
        affected_nodes  - secondary nodes impacted (e.g. buildings losing power)
        cascade_depth   - how many network hops this cascade has crossed
        metadata        - extra data (load value, pressure reading, etc.)
    """
    event_type:     EventType
    source_network: Network
    node_id:        str
    node_name:      Optional[str]    = None
    severity:       float            = 1.0
    tick:           int              = 0
    affected_nodes: List[str]        = field(default_factory=list)
    cascade_depth:  int              = 0
    metadata:       Dict[str, Any]   = field(default_factory=dict)

    # auto-generated
    event_id:   str   = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp:  float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id":       self.event_id,
            "timestamp":      self.timestamp,
            "tick":           self.tick,
            "event_type":     self.event_type.value,
            "source_network": self.source_network.value,
            "node_id":        self.node_id,
            "node_name":      self.node_name,
            "severity":       self.severity,
            "affected_nodes": self.affected_nodes,
            "cascade_depth":  self.cascade_depth,
            "metadata":       self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Event":
        return cls(
            event_type      = EventType(d["event_type"]),
            source_network  = Network(d["source_network"]),
            node_id         = d["node_id"],
            node_name       = d.get("node_name"),
            severity        = d.get("severity", 1.0),
            tick            = d.get("tick", 0),
            affected_nodes  = d.get("affected_nodes", []),
            cascade_depth   = d.get("cascade_depth", 0),
            metadata        = d.get("metadata", {}),
            event_id        = d.get("event_id", str(uuid.uuid4())[:8]),
            timestamp       = d.get("timestamp", time.time()),
        )

    def __repr__(self):
        return (
            f"Event({self.event_type.value} | "
            f"{self.source_network.value}:{self.node_id} | "
            f"sev={self.severity:.2f} | depth={self.cascade_depth} | tick={self.tick})"
        )


# ── convenience constructors ───────────────────────────────────────────────────

def node_failed(network: Network, node_id: str, tick: int,
                node_name: Optional[str] = None, cascade_depth: int = 0, **meta) -> Event:
    return Event(EventType.NODE_FAILED, network, node_id, node_name=node_name,
                 severity=1.0, tick=tick,
                 cascade_depth=cascade_depth, metadata=meta)

def node_degraded(network: Network, node_id: str, tick: int,
                  severity: float, node_name: Optional[str] = None, **meta) -> Event:
    return Event(EventType.NODE_DEGRADED, network, node_id, node_name=node_name,
                 severity=severity, tick=tick, metadata=meta)

def node_recovered(network: Network, node_id: str, tick: int, node_name: Optional[str] = None, **meta) -> Event:
    return Event(EventType.NODE_RECOVERED, network, node_id, node_name=node_name,
                 severity=0.0, tick=tick, metadata=meta)

def cascade_triggered(source_network: Network, source_node: str,
                      target_network: Network, target_node: str,
                      tick: int, depth: int, source_name: Optional[str] = None, **meta) -> Event:
    return Event(EventType.CASCADE_TRIGGERED, source_network, source_node, node_name=source_name,
                 severity=1.0, tick=tick,
                 affected_nodes=[target_node],
                 cascade_depth=depth,
                 metadata={"target_network": target_network.value,
                            "target_node": target_node, **meta})

def substation_failed(node_id: str, tick: int,
                      affected_transformers: List[str], **meta) -> Event:
    return Event(EventType.SUBSTATION_FAILED, Network.POWER, node_id,
                 severity=1.0, tick=tick,
                 affected_nodes=affected_transformers, metadata=meta)

def feeder_line_dropped(network: Network, node_id: str, tick: int,
                        affected_nodes: List[str], cascade_depth: int = 0,
                        **meta) -> Event:
    return Event(EventType.FEEDER_LINE_DROPPED, network, node_id,
                 severity=1.0, tick=tick,
                 affected_nodes=affected_nodes,
                 cascade_depth=cascade_depth, metadata=meta)

def pump_station_fail(node_id: str, tick: int, reason: str,
                      affected_towers: List[str], **meta) -> Event:
    return Event(EventType.PUMP_STATION_FAIL, Network.WATER, node_id,
                 severity=1.0, tick=tick,
                 affected_nodes=affected_towers,
                 metadata={"reason": reason, **meta})

def pressure_drop(node_id: str, tick: int, pressure: float,
                  cascade_depth: int = 0, **meta) -> Event:
    return Event(EventType.PRESSURE_DROP, Network.WATER, node_id,
                 severity=round(1.0 - pressure, 3), tick=tick,
                 cascade_depth=cascade_depth,
                 metadata={"pressure": pressure, **meta})

def cell_tower_battery(node_id: str, tick: int,
                       battery_remaining_kwh: float, **meta) -> Event:
    return Event(EventType.CELL_TOWER_BATTERY, Network.TELECOM, node_id,
                 severity=0.3, tick=tick,
                 metadata={"battery_remaining_kwh": battery_remaining_kwh, **meta})

def cell_tower_failed(node_id: str, tick: int,
                      coverage_radius_m: float, **meta) -> Event:
    return Event(EventType.CELL_TOWER_FAILED, Network.TELECOM, node_id,
                 severity=1.0, tick=tick,
                 metadata={"coverage_radius_m": coverage_radius_m, **meta})

def traffic_signal_ups(node_id: str, tick: int,
                       battery_remaining_h: float, **meta) -> Event:
    return Event(EventType.TRAFFIC_SIGNAL_UPS, Network.ROAD, node_id,
                 severity=0.2, tick=tick,
                 metadata={"battery_remaining_h": battery_remaining_h, **meta})

def traffic_signal_failed(node_id: str, tick: int, **meta) -> Event:
    return Event(EventType.TRAFFIC_SIGNAL_FAILED, Network.ROAD, node_id,
                 severity=1.0, tick=tick, metadata=meta)

def user_fail_node(network: Network, node_id: str, tick: int) -> Event:
    """Published by the frontend/API when user clicks 'fail this node'."""
    return Event(EventType.USER_FAIL_NODE, network, node_id,
                 severity=1.0, tick=tick,
                 metadata={"triggered_by": "user"})

def user_restore_node(network: Network, node_id: str, tick: int) -> Event:
    return Event(EventType.USER_RESTORE_NODE, network, node_id,
                 severity=0.0, tick=tick,
                 metadata={"triggered_by": "user"})

def sim_tick(tick: int) -> Event:
    return Event(EventType.SIMULATION_TICK, Network.SYSTEM, "sim",
                 severity=0.0, tick=tick)

def sim_end(tick: int, final_score: float) -> Event:
    return Event(EventType.SIMULATION_END, Network.SYSTEM, "sim",
                 severity=0.0, tick=tick,
                 metadata={"final_score": final_score})

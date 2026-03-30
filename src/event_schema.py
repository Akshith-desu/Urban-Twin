"""
Phase 2 - event_schema.py
Typed event definitions for the entire simulation.
Every agent uses these — never create raw dicts for events.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum
import time
import uuid


class EventType(str, Enum):
    # ── core state changes ────────────────────────────────────────────────────
    NODE_FAILED      = "NODE_FAILED"
    NODE_DEGRADED    = "NODE_DEGRADED"
    NODE_RECOVERED   = "NODE_RECOVERED"

    # ── cascade trigger ───────────────────────────────────────────────────────
    CASCADE_TRIGGERED = "CASCADE_TRIGGERED"

    # ── sector-specific ───────────────────────────────────────────────────────
    PRESSURE_DROP    = "PRESSURE_DROP"      # water
    LOAD_SPIKE       = "LOAD_SPIKE"         # power
    ROUTE_BLOCKED    = "ROUTE_BLOCKED"      # road
    SIGNAL_LOSS      = "SIGNAL_LOSS"        # telecom
    FLOOD_NODE       = "FLOOD_NODE"         # scenario injection

    # ── orchestration ─────────────────────────────────────────────────────────
    SIMULATION_TICK  = "SIMULATION_TICK"
    SIMULATION_END   = "SIMULATION_END"
    SCENARIO_START   = "SCENARIO_START"
    AGENT_REPORT     = "AGENT_REPORT"       # LLM situation report from agent


class Network(str, Enum):
    POWER   = "power"
    WATER   = "water"
    ROAD    = "road"
    TELECOM = "telecom"
    SYSTEM  = "system"           # used by orchestrator / simulation engine


@dataclass
class Event:
    """
    Single simulation event. All inter-agent communication uses this schema.

    Fields:
        event_id        - unique UUID, auto-generated
        timestamp       - unix timestamp, auto-generated
        tick            - simulation tick number (set by simulation engine)
        event_type      - one of EventType enum values
        source_network  - which network emitted this event
        node_id         - primary node affected (string ID)
        severity        - 0.0 (no impact) to 1.0 (complete failure)
        affected_nodes  - list of secondary nodes impacted
        cascade_depth   - how many network hops this cascade has crossed
        metadata        - any extra data (load value, pressure reading, etc.)
    """
    event_type:      EventType
    source_network:  Network
    node_id:         str
    severity:        float                    = 1.0
    tick:            int                      = 0
    affected_nodes:  List[str]                = field(default_factory=list)
    cascade_depth:   int                      = 0
    metadata:        Dict[str, Any]           = field(default_factory=dict)

    # auto-generated — do not set manually
    event_id:        str                      = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp:       float                    = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id":       self.event_id,
            "timestamp":      self.timestamp,
            "tick":           self.tick,
            "event_type":     self.event_type.value,
            "source_network": self.source_network.value,
            "node_id":        self.node_id,
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
            severity        = d.get("severity", 1.0),
            tick            = d.get("tick", 0),
            affected_nodes  = d.get("affected_nodes", []),
            cascade_depth   = d.get("cascade_depth", 0),
            metadata        = d.get("metadata", {}),
            event_id        = d.get("event_id", str(uuid.uuid4())[:8]),
            timestamp       = d.get("timestamp", time.time()),
        )

    def __repr__(self):
        return (f"Event({self.event_type.value} | "
                f"{self.source_network.value}:{self.node_id} | "
                f"sev={self.severity:.2f} | depth={self.cascade_depth} | "
                f"tick={self.tick})")


# ── convenience constructors ──────────────────────────────────────────────────

def node_failed(network: Network, node_id: str, tick: int,
                cascade_depth: int = 0, **meta) -> Event:
    return Event(EventType.NODE_FAILED, network, node_id,
                 severity=1.0, tick=tick,
                 cascade_depth=cascade_depth, metadata=meta)

def node_degraded(network: Network, node_id: str, tick: int,
                  severity: float, **meta) -> Event:
    return Event(EventType.NODE_DEGRADED, network, node_id,
                 severity=severity, tick=tick, metadata=meta)

def node_recovered(network: Network, node_id: str, tick: int, **meta) -> Event:
    return Event(EventType.NODE_RECOVERED, network, node_id,
                 severity=0.0, tick=tick, metadata=meta)

def cascade_triggered(source_network: Network, source_node: str,
                      target_network: Network, target_node: str,
                      tick: int, depth: int, **meta) -> Event:
    return Event(EventType.CASCADE_TRIGGERED, source_network, source_node,
                 severity=1.0, tick=tick,
                 affected_nodes=[target_node],
                 cascade_depth=depth,
                 metadata={"target_network": target_network.value,
                            "target_node": target_node, **meta})

def sim_tick(tick: int) -> Event:
    return Event(EventType.SIMULATION_TICK, Network.SYSTEM, "sim",
                 severity=0.0, tick=tick)

def sim_end(tick: int, final_score: float) -> Event:
    return Event(EventType.SIMULATION_END, Network.SYSTEM, "sim",
                 severity=0.0, tick=tick,
                 metadata={"final_score": final_score})
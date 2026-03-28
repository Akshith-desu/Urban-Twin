"""
Phase 2 — Async Event Bus
Communication backbone for the multi-agent urban digital twin(handles all the events).

- asyncio.Queue based (zero external dependencies)
- Typed event schema via dataclass
- Topic-based routing: agents subscribe by event_type
- Structured JSON event logger (file + in-memory buffer)
- Event replay tool for debugging simulation runs
- Key test: 10,000 events, zero dropped, correct delivery order per topic
"""

import asyncio
import json
import time
import uuid
import os
from dataclasses import dataclass, field, asdict
from typing import Callable, Coroutine, Any
from collections import defaultdict

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# EVENT SCHEMA
# ══════════════════════════════════════════════════════════════════════════════

# All valid event types — agents may only publish these
EVENT_TYPES = {
    # Core state changes (all agents)
    "NODE_FAILED",
    "NODE_DEGRADED",
    "NODE_RECOVERED",
    # Cross-network cascade
    "CASCADE_TRIGGERED",
    # Sector-specific
    "PRESSURE_DROP",       # water
    "LOAD_SPIKE",          # power
    "ROUTE_BLOCKED",       # road
    "SIGNAL_LOSS",         # telecom
    # Orchestration
    "SIMULATION_TICK",
    "SIMULATION_END",
    # Scenario injection
    "SCENARIO_INJECT",
}


@dataclass
class SimEvent:
    """
    Typed event schema. Every message on the bus is a SimEvent.
    Fields match the spec: event_id, timestamp, event_type, source_network,
    node_id, severity, affected_nodes, cascade_depth, metadata.
    """
    event_type: str                          # must be in EVENT_TYPES
    source_network: str                      # "power" | "water" | "road" | "telecom" | "orchestrator"
    node_id: str                             # which node triggered this event
    severity: float = 1.0                    # 0.0 (minor) → 1.0 (total failure)
    affected_nodes: list = field(default_factory=list)
    cascade_depth: int = 0                   # how many hops from original failure
    metadata: dict = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event_type '{self.event_type}'. Must be one of {EVENT_TYPES}")
        if not 0.0 <= self.severity <= 1.0:
            raise ValueError(f"severity must be between 0.0 and 1.0, got {self.severity}")

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @staticmethod
    def from_dict(d: dict) -> "SimEvent":
        return SimEvent(**d)


# ══════════════════════════════════════════════════════════════════════════════
# EVENT LOGGER
# ══════════════════════════════════════════════════════════════════════════════

class EventLogger:
    """
    Logs every event to:
    1. In-memory buffer (for replay and analysis within a run)
    2. JSON lines file on disk (for post-run analysis + paper figures)
    """

    def __init__(self, log_file: str = f"{LOG_DIR}/simulation_events.jsonl"):
        self.log_file = log_file
        self.buffer: list[SimEvent] = []
        self._file_handle = open(log_file, "w")
        print(f"  EventLogger: writing to {log_file}")

    def log(self, event: SimEvent):
        self.buffer.append(event)
        self._file_handle.write(event.to_json() + "\n")
        self._file_handle.flush()

    def close(self):
        self._file_handle.close()

    def get_buffer(self) -> list[SimEvent]:
        return list(self.buffer)

    def stats(self) -> dict:
        """Summary of what was logged — useful for paper reporting."""
        by_type = defaultdict(int)
        by_network = defaultdict(int)
        for e in self.buffer:
            by_type[e.event_type] += 1
            by_network[e.source_network] += 1
        return {
            "total_events": len(self.buffer),
            "by_type": dict(by_type),
            "by_network": dict(by_network),
            "duration_s": round(
                (self.buffer[-1].timestamp - self.buffer[0].timestamp), 3
            ) if len(self.buffer) >= 2 else 0,
        }


# ══════════════════════════════════════════════════════════════════════════════
# EVENT BUS
# ══════════════════════════════════════════════════════════════════════════════

class EventBus:
    """
    Async event bus using asyncio.Queue.

    - Agents subscribe by event_type (topic-based routing)
    - Each subscriber gets its own Queue — no shared state between agents
    - Publisher never blocks — uses put_nowait with overflow protection
    - All events logged via EventLogger
    """

    def __init__(self, logger: EventLogger = None, max_queue_size: int = 10000):
        # topic -> list of (subscriber_name, asyncio.Queue)
        self._subscriptions: dict[str, list[tuple[str, asyncio.Queue]]] = defaultdict(list)
        self._logger = logger
        self._max_queue_size = max_queue_size
        self._publish_count = 0
        self._dropped_count = 0

    def subscribe(self, event_types: list[str], subscriber_name: str) -> asyncio.Queue:
        """
        Register a subscriber for a list of event types.
        Returns a single Queue that will receive all matching events.
        Agents call this once at startup.
        """
        q = asyncio.Queue(maxsize=self._max_queue_size)
        for event_type in event_types:
            if event_type not in EVENT_TYPES:
                raise ValueError(f"Cannot subscribe to unknown event_type '{event_type}'")
            self._subscriptions[event_type].append((subscriber_name, q))
        return q

    def publish(self, event: SimEvent):
        """
        Publish an event to all subscribers of that event_type.
        Non-blocking — if a subscriber queue is full, event is dropped and counted.
        """
        self._publish_count += 1

        if self._logger:
            self._logger.log(event)

        subscribers = self._subscriptions.get(event.event_type, [])
        for name, q in subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self._dropped_count += 1
                print(f"  [BUS WARNING] Queue full for '{name}' — dropped event {event.event_id}")

    def stats(self) -> dict:
        return {
            "published": self._publish_count,
            "dropped": self._dropped_count,
            "subscribers": {
                etype: [name for name, _ in subs]
                for etype, subs in self._subscriptions.items()
            }
        }


# ══════════════════════════════════════════════════════════════════════════════
# EVENT REPLAY TOOL
# ══════════════════════════════════════════════════════════════════════════════

class EventReplayer:
    """
    Step through a recorded simulation run event-by-event.
    Loads from the JSONL log file written by EventLogger.
    Useful for debugging cascade chains after a run.
    """

    def __init__(self, log_file: str = f"{LOG_DIR}/simulation_events.jsonl"):
        self.events: list[SimEvent] = []
        self._cursor = 0

        if not os.path.exists(log_file):
            print(f"  No log file found at {log_file}")
            return

        with open(log_file, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    self.events.append(SimEvent.from_dict(json.loads(line)))

        print(f"  EventReplayer: loaded {len(self.events)} events from {log_file}")

    def reset(self):
        self._cursor = 0

    def next(self) -> SimEvent | None:
        """Step forward one event."""
        if self._cursor >= len(self.events):
            return None
        event = self.events[self._cursor]
        self._cursor += 1
        return event

    def peek(self) -> SimEvent | None:
        if self._cursor >= len(self.events):
            return None
        return self.events[self._cursor]

    def filter(self, event_type: str = None, network: str = None) -> list[SimEvent]:
        """Return filtered subset — useful for per-network cascade analysis."""
        result = self.events
        if event_type:
            result = [e for e in result if e.event_type == event_type]
        if network:
            result = [e for e in result if e.source_network == network]
        return result

    def print_cascade_chain(self, max_depth: int = 10):
        """Print the cascade chain — events ordered by cascade_depth."""
        chain = sorted(self.events, key=lambda e: (e.cascade_depth, e.timestamp))
        print(f"\n  Cascade chain ({len(chain)} events):")
        for e in chain[:max_depth]:
            print(f"    depth={e.cascade_depth} | {e.source_network:8} | "
                  f"{e.event_type:20} | node={e.node_id} | sev={e.severity:.2f}")
        if len(chain) > max_depth:
            print(f"    ... and {len(chain) - max_depth} more")

    def summary_by_tick(self) -> dict:
        """Group events by SIMULATION_TICK for per-tick analysis."""
        ticks = defaultdict(list)
        current_tick = 0
        for e in self.events:
            if e.event_type == "SIMULATION_TICK":
                current_tick = e.metadata.get("tick", current_tick + 1)
            else:
                ticks[current_tick].append(e)
        return dict(ticks)


# ══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS
# ══════════════════════════════════════════════════════════════════════════════

async def _test_basic_pubsub():
    """Test: publish → receive, correct routing."""
    print("\n[TEST 1] Basic pub/sub routing")
    logger = EventLogger(f"{LOG_DIR}/test_basic.jsonl")
    bus = EventBus(logger=logger)

    power_q = bus.subscribe(["NODE_FAILED", "LOAD_SPIKE"], "power_agent")
    water_q = bus.subscribe(["NODE_FAILED", "PRESSURE_DROP"], "water_agent")
    road_q  = bus.subscribe(["ROUTE_BLOCKED"], "road_agent")

    # publish a NODE_FAILED — should reach power + water, not road
    bus.publish(SimEvent(
        event_type="NODE_FAILED",
        source_network="power",
        node_id="PS-CMH",
        severity=1.0,
        metadata={"reason": "transformer_overload"}
    ))

    # publish a LOAD_SPIKE — only power should get it
    bus.publish(SimEvent(
        event_type="LOAD_SPIKE",
        source_network="power",
        node_id="PS-100FT",
        severity=0.7
    ))

    # publish a ROUTE_BLOCKED — only road
    bus.publish(SimEvent(
        event_type="ROUTE_BLOCKED",
        source_network="road",
        node_id="road_node_42",
        severity=0.5
    ))

    assert power_q.qsize() == 2, f"Power queue should have 2 events, got {power_q.qsize()}"
    assert water_q.qsize() == 1, f"Water queue should have 1 event, got {water_q.qsize()}"
    assert road_q.qsize()  == 1, f"Road queue should have 1 event, got {road_q.qsize()}"
    print("  PASS — routing correct, no cross-contamination")

    logger.close()


async def _test_10k_no_drops():
    """Key test: 10,000 events, zero dropped."""
    print("\n[TEST 2] 10,000 events — zero drops")
    logger = EventLogger(f"{LOG_DIR}/test_10k.jsonl")
    bus = EventBus(logger=logger, max_queue_size=15000)

    q = bus.subscribe(["NODE_FAILED"], "stress_subscriber")

    t0 = time.time()
    for i in range(10000):
        bus.publish(SimEvent(
            event_type="NODE_FAILED",
            source_network="power",
            node_id=f"node_{i}",
            severity=0.5
        ))
    elapsed = time.time() - t0

    assert bus.stats()["dropped"] == 0, f"Dropped events: {bus.stats()['dropped']}"
    assert q.qsize() == 10000, f"Queue size: {q.qsize()}, expected 10000"
    print(f"  PASS — 10,000 events published in {elapsed:.3f}s, zero dropped")
    print(f"  Throughput: {int(10000/elapsed):,} events/sec")

    logger.close()


async def _test_event_schema_validation():
    """Test: invalid event_type raises ValueError."""
    print("\n[TEST 3] Schema validation")
    try:
        SimEvent(event_type="MADE_UP_TYPE", source_network="power", node_id="X")
        assert False, "Should have raised ValueError"
    except ValueError:
        print("  PASS — invalid event_type rejected correctly")

    try:
        SimEvent(event_type="NODE_FAILED", source_network="power", node_id="X", severity=1.5)
        assert False, "Should have raised ValueError"
    except ValueError:
        print("  PASS — invalid severity rejected correctly")


async def _test_replay_tool():
    """Test: event replay loads and steps correctly."""
    print("\n[TEST 4] Event replay tool")
    logger = EventLogger(f"{LOG_DIR}/test_replay.jsonl")
    bus = EventBus(logger=logger)
    bus.subscribe(["NODE_FAILED"], "dummy")

    events_sent = []
    for i in range(5):
        e = SimEvent(
            event_type="NODE_FAILED",
            source_network="power",
            node_id=f"PS-{i}",
            severity=round(0.2 * i, 1),
            cascade_depth=i
        )
        bus.publish(e)
        events_sent.append(e.event_id)
    logger.close()

    replayer = EventReplayer(f"{LOG_DIR}/test_replay.jsonl")
    assert len(replayer.events) == 5

    # step through
    for i in range(5):
        e = replayer.next()
        assert e is not None
        assert e.event_id == events_sent[i]

    assert replayer.next() is None  # exhausted

    # filter test
    failed = replayer.filter(event_type="NODE_FAILED", network="power")
    assert len(failed) == 5

    print("  PASS — replay loaded, stepped, and filtered correctly")
    replayer.print_cascade_chain()


async def run_all_tests():
    print("=" * 55)
    print("PHASE 2: Event Bus — Unit Tests")
    print("=" * 55)
    await _test_basic_pubsub()
    await _test_10k_no_drops()
    await _test_event_schema_validation()
    await _test_replay_tool()

    print("\n" + "=" * 55)
    print("ALL TESTS PASSED — event bus ready for Phase 3 agents")
    print("=" * 55)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
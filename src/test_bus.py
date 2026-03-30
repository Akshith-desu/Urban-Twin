"""
Phase 2 - test_bus.py
Tests for the event bus. Run this to confirm Phase 2 is working before Phase 3.

Run:  python src/test_bus.py
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from event_schema import (
    Event, EventType, Network,
    node_failed, node_degraded, node_recovered,
    cascade_triggered, sim_tick, sim_end
)
from event_bus import EventBus
from event_logger import EventLogger, load_event_log


# ── helpers ───────────────────────────────────────────────────────────────────

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
results = {}

def check(name: str, condition: bool, detail: str = ""):
    icon = PASS if condition else FAIL
    status = "PASS" if condition else "FAIL"
    print(f"  [{icon}] {name}: {status} {detail}")
    results[name] = condition
    return condition


# ── test 1: event schema ──────────────────────────────────────────────────────

def test_event_schema():
    print("\nTest 1: Event schema")

    e = node_failed(Network.POWER, "PS-CMH", tick=5, reason="flood")
    check("Event has event_id",   bool(e.event_id))
    check("Event has timestamp",  e.timestamp > 0)
    check("Event type correct",   e.event_type == EventType.NODE_FAILED)
    check("Network correct",      e.source_network == Network.POWER)
    check("Node ID correct",      e.node_id == "PS-CMH")
    check("Tick correct",         e.tick == 5)
    check("Metadata preserved",   e.metadata.get("reason") == "flood")

    # round-trip
    d = e.to_dict()
    e2 = Event.from_dict(d)
    check("Round-trip event_type",  e2.event_type == e.event_type)
    check("Round-trip node_id",     e2.node_id == e.node_id)
    check("Round-trip metadata",    e2.metadata == e.metadata)


# ── test 2: publish + subscribe ───────────────────────────────────────────────

async def test_pubsub():
    print("\nTest 2: Publish + subscribe")

    bus = EventBus(log_path="data/test_events.jsonl")
    await bus.start()

    # subscribe power agent to NODE_FAILED only
    bus.subscribe("power_agent", [EventType.NODE_FAILED, EventType.FLOOD_NODE])
    # subscribe orchestrator to ALL
    orch_queue = bus.subscribe_all("orchestrator")

    received_power = []
    received_orch  = []

    # publish mix of events
    events_to_send = [
        node_failed(Network.POWER, "PS-CMH",  tick=1),
        node_degraded(Network.WATER, "WP-MAIN", tick=1, severity=0.6),
        node_failed(Network.ROAD,  "R-001",   tick=2),
        Event(EventType.FLOOD_NODE, Network.SYSTEM, "flood-zone-1", tick=2),
        node_recovered(Network.POWER, "PS-CMH", tick=3),
    ]

    for ev in events_to_send:
        await bus.publish(ev)

    # let router process
    await asyncio.sleep(0.05)

    # drain power_agent queues
    pq = bus._subscribers["power_agent"]
    for et, q in pq.items():
        while not q.empty():
            received_power.append(q.get_nowait())

    # drain orchestrator queue
    while not orch_queue.empty():
        received_orch.append(orch_queue.get_nowait())

    check("Power agent got NODE_FAILED events",
          any(e.event_type == EventType.NODE_FAILED for e in received_power))
    check("Power agent got FLOOD_NODE event",
          any(e.event_type == EventType.FLOOD_NODE for e in received_power))
    check("Power agent did NOT get NODE_DEGRADED (not subscribed)",
          all(e.event_type != EventType.NODE_DEGRADED for e in received_power))
    check("Orchestrator got ALL 5 events",
          len(received_orch) == 5, f"(got {len(received_orch)})")
    check("Bus stats published=5",
          bus.published_count == 5, f"(published={bus.published_count})")

    await bus.stop()


# ── test 3: high volume ───────────────────────────────────────────────────────

async def test_high_volume():
    print("\nTest 3: High volume (10,000 events)")

    bus = EventBus(log_path="data/test_volume.jsonl", maxsize=20_000)
    await bus.start()

    orch_queue = bus.subscribe_all("orchestrator", queue_size=20_000)

    N = 10_000
    t0 = time.time()

    for i in range(N):
        await bus.publish(node_failed(Network.POWER, f"node-{i}", tick=i % 100))

    await asyncio.wait_for(bus._master_queue.join(), timeout=10.0)   # let router flush

    elapsed = time.time() - t0
    throughput = N / elapsed

    drained = 0
    while not orch_queue.empty():
        orch_queue.get_nowait()
        drained += 1

    check("Zero dropped events",
          bus.dropped_count == 0, f"(dropped={bus.dropped_count})")
    check("All 10k events received by orchestrator",
          drained == N, f"(received={drained})")
    check("Throughput > 5000 events/sec",
          throughput > 5000, f"({throughput:.0f} ev/s)")

    await bus.stop()
    print(f"    Throughput: {throughput:.0f} events/sec")


# ── test 4: event logger ──────────────────────────────────────────────────────

async def test_event_logger():
    print("\nTest 4: Event logger")

    log_path = "data/test_logger.jsonl"
    bus = EventBus(log_path=log_path)
    await bus.start()

    events_sent = [
        node_failed(Network.POWER,   "PS-1",  tick=1),
        node_failed(Network.WATER,   "WP-1",  tick=2),
        node_degraded(Network.ROAD,  "R-1",   tick=3, severity=0.5),
        node_recovered(Network.TELECOM, "T-1", tick=4),
    ]
    for e in events_sent:
        await bus.publish(e)

    await asyncio.sleep(0.05)
    await bus.stop()

    # reload from disk
    loaded = load_event_log(log_path)
    check("Logger wrote all 4 events to disk",
          len(loaded) == 4, f"(found {len(loaded)})")
    check("Events are valid JSON with event_type",
          all("event_type" in e for e in loaded))
    check("Events have timestamps",
          all("timestamp" in e for e in loaded))
    check("Network fields preserved",
          loaded[0]["source_network"] == "power")


# ── test 5: cascade event ─────────────────────────────────────────────────────

async def test_cascade_event():
    print("\nTest 5: Cascade event schema")

    bus = EventBus(log_path="data/test_cascade.jsonl")
    await bus.start()

    orch_q = bus.subscribe_all("orchestrator")

    cascade = cascade_triggered(
        source_network=Network.POWER,
        source_node="PS-CMH",
        target_network=Network.WATER,
        target_node="WP-MAIN",
        tick=3,
        depth=1,
        reason="substation_failed"
    )
    await bus.publish(cascade)
    await asyncio.sleep(0.05)

    received = []
    while not orch_q.empty():
        received.append(orch_q.get_nowait())

    await bus.stop()

    check("Cascade event received",        len(received) == 1)
    check("Cascade depth is 1",            received[0].cascade_depth == 1)
    check("Cascade target in metadata",    received[0].metadata.get("target_node") == "WP-MAIN")
    check("Cascade target network in meta", received[0].metadata.get("target_network") == "water")


# ── test 6: sim tick + end ────────────────────────────────────────────────────

async def test_sim_lifecycle():
    print("\nTest 6: Simulation lifecycle events")

    bus = EventBus(log_path="data/test_lifecycle.jsonl")
    await bus.start()
    orch_q = bus.subscribe_all("orchestrator")

    await bus.publish(sim_tick(tick=1))
    await bus.publish(sim_tick(tick=2))
    await bus.publish(sim_end(tick=2, final_score=73.4))
    await asyncio.sleep(0.05)

    received = []
    while not orch_q.empty():
        received.append(orch_q.get_nowait())

    await bus.stop()

    tick_events = [e for e in received if e.event_type == EventType.SIMULATION_TICK]
    end_events  = [e for e in received if e.event_type == EventType.SIMULATION_END]

    check("Received 2 tick events",   len(tick_events) == 2)
    check("Received 1 end event",     len(end_events)  == 1)
    check("Final score in end event", end_events[0].metadata.get("final_score") == 73.4)


# ── main ──────────────────────────────────────────────────────────────────────

async def run_all_tests():
    os.makedirs("data", exist_ok=True)

    print("=" * 55)
    print("PHASE 2 TEST SUITE — Event Bus")
    print("=" * 55)

    test_event_schema()
    await test_pubsub()
    await test_high_volume()
    await test_event_logger()
    await test_cascade_event()
    await test_sim_lifecycle()

    passed = sum(results.values())
    total  = len(results)
    print(f"\n{'='*55}")
    print(f"Results: {passed}/{total} checks passed")

    if passed == total:
        print("Phase 2 COMPLETE — event bus ready for Phase 3 agents")
    else:
        failed = [k for k, v in results.items() if not v]
        print(f"Failed: {failed}")

    return passed == total


if __name__ == "__main__":
    ok = asyncio.run(run_all_tests())
    sys.exit(0 if ok else 1)
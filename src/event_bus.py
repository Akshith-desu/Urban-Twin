"""
Phase 2 - event_bus.py
Async event bus using asyncio.Queue with topic-based routing.
Zero external dependencies — no Redis needed for Phase 2.

Architecture:
  - One master queue receives ALL published events
  - A router task reads from master queue and fans out to per-topic subscriber queues
  - Each agent gets its own asyncio.Queue per subscribed topic
  - EventLogger taps every routed event to disk

Usage:
    bus = EventBus()
    await bus.start()

    # subscribe (returns an async generator)
    async for event in bus.subscribe("my_agent", [EventType.NODE_FAILED]):
        handle(event)

    # publish
    await bus.publish(node_failed(Network.POWER, "PS-7", tick=3))

    await bus.stop()
"""

import asyncio
import logging
from typing import List, Dict, Set, AsyncGenerator, Optional
from collections import defaultdict

from event_schema import Event, EventType, Network, sim_tick, sim_end
from event_logger import EventLogger

logger = logging.getLogger(__name__)


class EventBus:
    """
    Central async event bus for the simulation.

    Each subscriber registers a name + list of EventTypes it cares about.
    The router delivers only matching events to each subscriber's queue.
    The orchestrator subscribes to ALL event types.
    """

    def __init__(self, log_path: str = "data/events.jsonl", maxsize: int = 10_000):
        self._master_queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

        # subscriber_id -> { event_type -> asyncio.Queue }
        self._subscribers: Dict[str, Dict[EventType, asyncio.Queue]] = defaultdict(dict)

        # subscriber_id -> set of subscribed EventTypes
        self._subscriptions: Dict[str, Set[EventType]] = defaultdict(set)

        self._event_logger = EventLogger(log_path)
        self._router_task: Optional[asyncio.Task] = None
        self._running = False

        # stats
        self.published_count = 0
        self.routed_count = 0
        self.dropped_count = 0

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self):
        """Start the router task. Call before any publish/subscribe."""
        self._running = True
        self._event_logger.open()
        self._router_task = asyncio.create_task(self._router(), name="event_router")
        logger.info("EventBus started")

    async def stop(self):
        """Drain the queue and shut down cleanly."""
        self._running = False
        # sentinel to unblock router
        await self._master_queue.put(None)
        if self._router_task:
            try:
                await asyncio.wait_for(self._router_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._router_task.cancel()
        self._event_logger.close()
        logger.info(f"EventBus stopped. published={self.published_count} "
                    f"routed={self.routed_count} dropped={self.dropped_count}")

    # ── subscribe ─────────────────────────────────────────────────────────────

    def subscribe(self, subscriber_id: str,
                  event_types: List[EventType],
                  queue_size: int = 1000) -> Dict[EventType, asyncio.Queue]:
        """
        Register a subscriber and return its queue dict.
        Call BEFORE bus.start() or at any time before events start flowing.

        Returns: { EventType -> asyncio.Queue }
        """
        for et in event_types:
            if et not in self._subscribers[subscriber_id]:
                self._subscribers[subscriber_id][et] = asyncio.Queue(maxsize=queue_size)
            self._subscriptions[subscriber_id].add(et)

        logger.debug(f"Subscriber '{subscriber_id}' registered for {[e.value for e in event_types]}")
        return self._subscribers[subscriber_id]

    def subscribe_all(self, subscriber_id: str, queue_size: int = 5000) -> asyncio.Queue:
        """
        Subscribe to ALL event types (used by orchestrator).
        Returns a single queue that receives every event.
        """
        q = asyncio.Queue(maxsize=queue_size)
        self._subscribers[subscriber_id]["__all__"] = q
        self._subscriptions[subscriber_id].add("__all__")
        return q

    async def get_events(self, subscriber_id: str,
                         event_types: List[EventType]) -> AsyncGenerator[Event, None]:
        """
        Async generator — yield events as they arrive for this subscriber.
        Use in an async for loop inside each agent's run() method.

        Example:
            async for event in bus.get_events("power_agent", [EventType.FLOOD_NODE]):
                await self.handle(event)
        """
        queues = self._subscribers.get(subscriber_id, {})
        while self._running:
            # poll all relevant queues with a short timeout
            for et in event_types:
                q = queues.get(et)
                if q:
                    try:
                        event = q.get_nowait()
                        yield event
                    except asyncio.QueueEmpty:
                        pass
            await asyncio.sleep(0.001)   # 1ms poll interval

    # ── publish ───────────────────────────────────────────────────────────────

    async def publish(self, event: Event):
        """
        Publish an event to the bus.
        Non-blocking — drops event and increments dropped_count if master queue is full.
        """
        try:
            self._master_queue.put_nowait(event)
            self.published_count += 1
        except asyncio.QueueFull:
            self.dropped_count += 1
            logger.warning(f"Master queue full — dropped event {event.event_type.value} "
                           f"from {event.source_network.value}:{event.node_id}")

    async def publish_many(self, events: List[Event]):
        """Publish a batch of events."""
        for event in events:
            await self.publish(event)

    # ── router ────────────────────────────────────────────────────────────────

    async def _router(self):
        """
        Core routing loop. Reads from master queue, fans out to subscriber queues.
        Runs as a background asyncio task.
        """

        while True:
            try:
                event = await asyncio.wait_for(
                    self._master_queue.get(), timeout=0.1
                )
            except asyncio.TimeoutError:
                if not self._running:
                    break
                continue

            # None is the stop sentinel
            if event is None:
                break

            # log every event
            self._event_logger.log(event)

            # fan out to matching subscribers
            for sub_id, sub_queues in self._subscribers.items():
                # all-events subscriber (orchestrator)
                if "__all__" in sub_queues:
                    try:
                        sub_queues["__all__"].put_nowait(event)
                        self.routed_count += 1
                    except asyncio.QueueFull:
                        pass

                # topic-filtered subscribers
                et_queue = sub_queues.get(event.event_type)
                if et_queue:
                    try:
                        et_queue.put_nowait(event)
                        self.routed_count += 1
                    except asyncio.QueueFull:
                        logger.warning(f"Queue full for {sub_id}:{event.event_type.value}")

            self._master_queue.task_done()

    # ── helpers ───────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "published": self.published_count,
            "routed":    self.routed_count,
            "dropped":   self.dropped_count,
            "queue_size": self._master_queue.qsize(),
            "subscribers": list(self._subscribers.keys()),
        }

    def queue_depth(self, subscriber_id: str) -> dict:
        """Return queue depths for a subscriber — useful for debugging backpressure."""
        queues = self._subscribers.get(subscriber_id, {})
        return {str(et): q.qsize() for et, q in queues.items()}

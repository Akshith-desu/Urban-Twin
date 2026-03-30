"""
Phase 2 - event_logger.py
Writes every event to a JSON-lines file for post-simulation replay and analysis.
One line per event — easy to stream, easy to parse.
"""

import json
import os
import logging
from typing import List
from event_schema import Event

logger = logging.getLogger(__name__)


class EventLogger:
    """
    Logs all bus events to a .jsonl file (one JSON object per line).
    Thread-safe enough for asyncio (single-threaded event loop).
    Also keeps an in-memory buffer for fast access during simulation.
    """

    def __init__(self, path: str = "data/events.jsonl", buffer_limit: int = 50_000):
        self.path = path
        self.buffer_limit = buffer_limit
        self._buffer: List[dict] = []
        self._file = None
        self._count = 0
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def open(self):
        self._file = open(self.path, "w", encoding="utf-8")
        logger.info(f"EventLogger opened: {self.path}")

    def close(self):
        if self._file:
            self._file.flush()
            self._file.close()
            self._file = None
        logger.info(f"EventLogger closed. Total events logged: {self._count}")

    def log(self, event: Event):
        d = event.to_dict()
        # write to file
        if self._file:
            self._file.write(json.dumps(d) + "\n")
        # keep in memory buffer (drop oldest if full)
        if len(self._buffer) < self.buffer_limit:
            self._buffer.append(d)
        self._count += 1

    def flush(self):
        if self._file:
            self._file.flush()

    def get_buffer(self) -> List[dict]:
        return list(self._buffer)

    def events_by_type(self, event_type: str) -> List[dict]:
        return [e for e in self._buffer if e["event_type"] == event_type]

    def events_by_network(self, network: str) -> List[dict]:
        return [e for e in self._buffer if e["source_network"] == network]

    def events_in_tick_range(self, start: int, end: int) -> List[dict]:
        return [e for e in self._buffer if start <= e["tick"] <= end]

    @property
    def count(self) -> int:
        return self._count


# ─────────────────────────────────────────────────────────────────────────────

def load_event_log(path: str) -> List[dict]:
    """Load a saved .jsonl event log from disk."""
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events
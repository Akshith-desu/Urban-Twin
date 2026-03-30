"""
Phase 2 - replay.py
Step through a saved simulation run event by event.
Useful for debugging cascade chains and verifying agent behaviour.

Run:  python src/replay.py data/events.jsonl
"""

import json
import sys
import os
from typing import List, Optional
from event_logger import load_event_log


# ANSI colors for terminal output
COLORS = {
    "power":   "\033[93m",   # yellow
    "water":   "\033[94m",   # blue
    "road":    "\033[92m",   # green
    "telecom": "\033[95m",   # magenta
    "system":  "\033[90m",   # gray
    "reset":   "\033[0m",
}

SEVERITY_ICONS = {
    (0.0, 0.3):  "○",   # healthy / recovering
    (0.3, 0.7):  "◔",   # degraded
    (0.7, 1.01): "●",   # failed
}

def severity_icon(s: float) -> str:
    for (lo, hi), icon in SEVERITY_ICONS.items():
        if lo <= s < hi:
            return icon
    return "●"


def format_event(e: dict, idx: int) -> str:
    net = e.get("source_network", "system")
    color = COLORS.get(net, "")
    reset = COLORS["reset"]
    icon = severity_icon(e.get("severity", 1.0))

    meta_str = ""
    meta = e.get("metadata", {})
    if meta:
        meta_str = "  " + "  ".join(f"{k}={v}" for k, v in list(meta.items())[:3])

    affected = e.get("affected_nodes", [])
    affected_str = f"  → {affected[:3]}" if affected else ""

    return (
        f"[{idx:04d}] tick={e['tick']:03d}  "
        f"{color}{icon} {e['event_type']:<22}{reset}  "
        f"{net:<8}  {e['node_id']:<20}  "
        f"sev={e.get('severity', 0):.2f}  depth={e.get('cascade_depth', 0)}"
        f"{affected_str}{meta_str}"
    )


class SimulationReplayer:
    """
    Interactive replay of a saved simulation run.
    Supports stepping forward/back, filtering by network or event type,
    jumping to a specific tick, and showing cascade chains.
    """

    def __init__(self, events: List[dict]):
        self.events = events
        self.pos = 0

    @classmethod
    def from_file(cls, path: str) -> "SimulationReplayer":
        events = load_event_log(path)
        print(f"Loaded {len(events)} events from {path}")
        return cls(events)

    def current(self) -> Optional[dict]:
        if 0 <= self.pos < len(self.events):
            return self.events[self.pos]
        return None

    def step(self, n: int = 1):
        self.pos = max(0, min(self.pos + n, len(self.events) - 1))

    def jump_to_tick(self, tick: int):
        for i, e in enumerate(self.events):
            if e["tick"] >= tick:
                self.pos = i
                return
        self.pos = len(self.events) - 1

    def show_range(self, start: int, end: int):
        for i in range(max(0, start), min(end + 1, len(self.events))):
            print(format_event(self.events[i], i))

    def show_cascade_chain(self, event_idx: int, depth_limit: int = 10):
        """Show all events caused by the cascade starting at event_idx."""
        root = self.events[event_idx]
        root_tick = root["tick"]
        root_node = root["node_id"]
        print(f"\nCascade chain from event {event_idx} "
              f"(tick={root_tick}, node={root_node}):")
        print(format_event(root, event_idx))

        depth = 0
        current_affected = set(root.get("affected_nodes", [root_node]))

        for i in range(event_idx + 1, len(self.events)):
            e = self.events[i]
            if e["tick"] > root_tick + depth_limit:
                break
            if (e["node_id"] in current_affected or
                    e.get("cascade_depth", 0) > depth):
                print("  " + format_event(e, i))
                current_affected.update(e.get("affected_nodes", []))
                depth = max(depth, e.get("cascade_depth", 0))

    def summary(self) -> dict:
        from collections import Counter
        type_counts  = Counter(e["event_type"] for e in self.events)
        net_counts   = Counter(e["source_network"] for e in self.events)
        ticks        = [e["tick"] for e in self.events]
        max_depth    = max((e.get("cascade_depth", 0) for e in self.events), default=0)
        failures     = [e for e in self.events if e["event_type"] == "NODE_FAILED"]
        return {
            "total_events":    len(self.events),
            "ticks_range":     (min(ticks), max(ticks)) if ticks else (0, 0),
            "by_type":         dict(type_counts.most_common()),
            "by_network":      dict(net_counts.most_common()),
            "max_cascade_depth": max_depth,
            "total_failures":  len(failures),
            "networks_affected": list(set(e["source_network"] for e in failures)),
        }

    def interactive(self):
        """Run an interactive terminal replay session."""
        print("\n=== Simulation Replay ===")
        print("Commands: n (next), p (prev), N<num> (skip N), t<tick> (jump to tick),")
        print("          c (cascade chain), s (summary), q (quit), ?<network> (filter)")
        print()

        while True:
            e = self.current()
            if e:
                print(format_event(e, self.pos))

            try:
                cmd = input(f"[{self.pos}/{len(self.events)-1}] > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting replay.")
                break

            if cmd == "q":
                break
            elif cmd == "n" or cmd == "":
                self.step(1)
            elif cmd == "p":
                self.step(-1)
            elif cmd.startswith("N") and cmd[1:].isdigit():
                self.step(int(cmd[1:]))
            elif cmd.startswith("t") and cmd[1:].isdigit():
                self.jump_to_tick(int(cmd[1:]))
                print(f"Jumped to tick {int(cmd[1:])}")
            elif cmd == "c":
                self.show_cascade_chain(self.pos)
            elif cmd == "s":
                s = self.summary()
                print("\nSummary:")
                for k, v in s.items():
                    print(f"  {k}: {v}")
                print()
            elif cmd.startswith("?"):
                net = cmd[1:]
                filtered = [(i, e) for i, e in enumerate(self.events)
                            if e.get("source_network") == net]
                print(f"\nEvents from network '{net}': {len(filtered)}")
                for i, ev in filtered[:20]:
                    print(format_event(ev, i))
                print()
            else:
                print("Unknown command. Use n/p/N<num>/t<tick>/c/s/q/?<network>")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/events.jsonl"
    if not os.path.exists(path):
        print(f"Event log not found: {path}")
        print("Run a simulation first (Phase 3+) to generate an event log.")
        sys.exit(1)

    replayer = SimulationReplayer.from_file(path)

    print("\nSummary:")
    for k, v in replayer.summary().items():
        print(f"  {k}: {v}")

    replayer.interactive()
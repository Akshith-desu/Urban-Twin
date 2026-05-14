import logging
from typing import Optional
from .base_agent import BaseAgent
from event_schema import Event, EventType, Network

logger = logging.getLogger(__name__)

class RoadAgent(BaseAgent):
    """
    Manages the Road Network.
    Reacts to floods and signal failures.
    """

    def __init__(self, bus):
        super().__init__(Network.ROAD, bus)

    async def handle_failure(self, event: Event, node_id: Optional[str] = None):
        """Standard failure plus route blocked emission."""
        nid = node_id or event.node_id
        was_failed = self.state.get(nid, {}).get("is_failed", False)
        
        await super().handle_failure(event, node_id)
        
        if nid in self.state and not was_failed:
            # Emit route blocked event
            await self.publish(Event(
                EventType.ROUTE_BLOCKED,
                Network.ROAD,
                nid,
                severity=1.0,
                cascade_depth=event.cascade_depth + 1,
                metadata={"reason": "signal_failure" if node_id else "direct_flood"}
            ))

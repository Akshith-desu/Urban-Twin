import logging
from typing import Optional
from .base_agent import BaseAgent
from event_schema import Event, EventType, Network

logger = logging.getLogger(__name__)

class WaterAgent(BaseAgent):
    """
    Manages the Water Network.
    Reacts to power outages (pumps stopping) and triggers pressure drops.
    """

    def __init__(self, bus):
        super().__init__(Network.WATER, bus)

    async def handle_failure(self, event: Event, node_id: Optional[str] = None):
        """Standard failure plus pressure drop emission."""
        nid = node_id or event.node_id
        was_failed = self.state.get(nid, {}).get("is_failed", False)
        
        await super().handle_failure(event, node_id)
        
        if nid in self.state and not was_failed:
            # Also emit pressure drop event
            await self.publish(Event(
                EventType.PRESSURE_DROP,
                Network.WATER,
                nid,
                severity=0.8,
                cascade_depth=event.cascade_depth + 1,
                metadata={"reason": "pump_failure"}
            ))

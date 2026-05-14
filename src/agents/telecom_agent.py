import logging
from typing import Optional
from .base_agent import BaseAgent
from event_schema import Event, EventType, Network

logger = logging.getLogger(__name__)

class TelecomAgent(BaseAgent):
    """
    Manages the Telecom Network.
    Reacts to power outages (signal loss) and triggers cascades to Road (smart signals).
    """

    def __init__(self, bus):
        super().__init__(Network.TELECOM, bus)

    async def handle_failure(self, event: Event, node_id: Optional[str] = None):
        """Standard failure plus signal loss emission."""
        # Store if it was already failed to avoid double emission
        nid = node_id or event.node_id
        was_failed = self.state.get(nid, {}).get("is_failed", False)
        
        await super().handle_failure(event, node_id)
        
        if nid in self.state and not was_failed:
            # Emit signal loss event as well
            await self.publish(Event(
                EventType.SIGNAL_LOSS,
                Network.TELECOM,
                nid,
                severity=1.0,
                cascade_depth=event.cascade_depth + 1,
                metadata={"reason": "cascaded_failure" if node_id else "direct_failure"}
            ))

import logging
from typing import List

from .base_agent import BaseAgent
from event_schema import Network

logger = logging.getLogger(__name__)

class PowerAgent(BaseAgent):
    """
    Manages the Power Grid.
    Triggers cascades to Water (pumps), Telecom (SCADA), and Road (signals) when power is lost.
    """

    def __init__(self, bus):
        super().__init__(Network.POWER, bus)
        self.load_level = 0.65 # Initial system load

    async def handle_tick(self, tick: int):
        """Power-specific logic: grid stability checks."""
        await super().handle_tick(tick)
        
        # Count failed nodes
        failed_count = sum(1 for n in self.state.values() if n["is_failed"])
        if failed_count > len(self.state) * 0.3:
            # If >30% failed, increase load on others
            self.load_level = min(1.0, self.load_level + 0.05)
            logger.warning(f"Agent {self.name}: System Load Spike! Level: {self.load_level:.2f}")
            
            if self.load_level > 0.9:
                 logger.error(f"Agent {self.name}: CRITICAL LOAD! Triggering brownout cascades.")
                 # (In a real sim, we would pick a random node to fail)

    # Note: Dependencies and cascade handling are now managed by BaseAgent.

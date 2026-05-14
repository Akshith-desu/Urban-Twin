import asyncio
import logging
from typing import List, Dict, Any

from event_bus import EventBus
from event_schema import Event, EventType, Network, sim_tick, sim_end
from agents import PowerAgent, TelecomAgent, WaterAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("orchestrator")

class SimulationOrchestrator:
    """
    Drives the simulation. Manages agents and simulation time.
    """

    def __init__(self, bus: EventBus):
        self.bus = bus
        self.agents = [
            PowerAgent(bus),
            TelecomAgent(bus),
            WaterAgent(bus)
        ]
        self.current_tick = 0
        self.running = False

    async def start(self):
        """Start the bus and all agents."""
        await self.bus.start()
        for agent in self.agents:
            await agent.start()
            # Start agent's event loop in background
            asyncio.create_task(agent.run())
        
        self.running = True
        logger.info(f"Simulation Orchestrator started with {len(self.agents)} agents.")

    async def stop(self):
        """Stop everything."""
        self.running = False
        await self.bus.publish(sim_end(self.current_tick, 0.0))
        await asyncio.sleep(0.5) # Wait for event to propagate
        await self.bus.stop()
        logger.info("Simulation Orchestrator stopped.")

    async def run_step(self):
        """Advance simulation by one tick."""
        self.current_tick += 1
        logger.info(f"--- TICK {self.current_tick} ---")
        
        # Publish tick event
        await self.bus.publish(sim_tick(self.current_tick))
        
        # Let events propagate
        await asyncio.sleep(0.2)

    async def inject_scenario(self, scenario_type: str, params: dict):
        """Inject a scenario (e.g. flood)."""
        print(f"Orchestrator: Injecting {scenario_type} for nodes {params.get('nodes')}")
        logger.info(f"Injecting scenario: {scenario_type} with params {params}")
        
        if scenario_type == "flood":
            # In a real sim, we would find nodes in the flood zone
            nodes = params.get("nodes", [])
            names = params.get("names", {})
            for nid in nodes:
                await self.bus.publish(Event(
                    EventType.FLOOD_NODE,
                    Network.SYSTEM,
                    str(nid),
                    node_name=names.get(nid),
                    severity=1.0,
                    tick=self.current_tick,
                    metadata={"reason": "flood_injection"}
                ))
        
        elif scenario_type == "recovery":
            # Recover specified nodes
            nodes = params.get("nodes", [])
            names = params.get("names", {})
            for nid in nodes:
                await self.bus.publish(Event(
                    EventType.NODE_RECOVERED,
                    Network.SYSTEM,
                    str(nid),
                    node_name=names.get(nid),
                    severity=0.0,
                    tick=self.current_tick,
                    metadata={"reason": "manual_recovery"}
                ))

async def test_run():
    bus = EventBus()
    orch = SimulationOrchestrator(bus)
    await orch.start()
    
    # Tick 1: Normal
    await orch.run_step()
    
    # Tick 2: Inject failure
    # Assuming some node IDs from Phase 1 (e.g. PS-CMH for power)
    await orch.inject_scenario("flood", {"nodes": ["PS-CMH"]})
    await orch.run_step()
    
    # Tick 3: Observe cascades
    await orch.run_step()
    
    await orch.stop()

if __name__ == "__main__":
    asyncio.run(test_run())

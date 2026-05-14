import asyncio
import logging
from orchestrator import SimulationOrchestrator
from event_bus import EventBus

async def main():
    logging.basicConfig(level=logging.INFO)
    bus = EventBus()
    orch = SimulationOrchestrator(bus)
    
    await orch.start()
    
    # Tick 1: Normal operations
    await orch.run_step()
    
    # Tick 2: Inject a flood at a power substation (PS-CMH is in the power network)
    print("\n--- INJECTING FLOOD AT PS-CMH ---")
    await orch.inject_scenario("flood", {"nodes": ["PS-CMH"]})
    await orch.run_step()
    
    # Tick 3: Observe cascades (Water pumps, etc.)
    await orch.run_step()
    
    # Tick 4: Simulation End
    await orch.run_step()
    
    await orch.stop()

if __name__ == "__main__":
    asyncio.run(main())

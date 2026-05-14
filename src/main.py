import asyncio

from event_bus import EventBus
from power_agent import PowerAgent
from water_agent import WaterAgent

from event_schema import (
    sim_tick,
    user_fail_node,
    Network
)


async def main():

    # create bus
    bus = EventBus()

    # start bus
    await bus.start()

    # create agents
    power_agent = PowerAgent(bus)
    water_agent = WaterAgent(bus)

    # start agents
    asyncio.create_task(power_agent.start())
    asyncio.create_task(water_agent.start())

    print("Simulation started")

    for tick in range(20):

        print(f"\n===== TICK {tick} =====")

        # send simulation tick
        await bus.publish(sim_tick(tick))

        # FAIL A NODE AT TICK 2
        if tick == 2:

            print("FAILING NODE")

            await bus.publish(
                user_fail_node(
                    Network.POWER,
                    "substation_1",   # change this if needed
                    tick
                )
            )

        await asyncio.sleep(1)

    print("Simulation finished")

    await bus.stop()


asyncio.run(main())
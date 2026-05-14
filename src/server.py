import asyncio
import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from event_bus import EventBus
from event_schema import Event, EventType, Network, user_fail_node, sim_tick
from power_agent import PowerAgent
from water_agent import WaterAgent
from telecom_agent import TelecomAgent

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("UrbanTwinServer")

app = FastAPI(title="Urban Twin Simulation Server")

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── graph file paths ─────────────────────────────────────────────────────────
POWER_JSON   = "src/graphs/power.json"
WATER_JSON   = "src/graphs/water.json"
TELECOM_JSON = "src/graphs/telecom.json"

# ── simulation state (module-level, rebuilt on /reset) ───────────────────────
bus: EventBus = EventBus()
power_agent:   PowerAgent   = None
water_agent:   WaterAgent   = None
telecom_agent: TelecomAgent = None

# background tasks so we can cancel them on reset
_agent_tasks: list = []
_sim_task:    asyncio.Task = None


def _build_agents():
    """Construct a fresh bus + agents. Does NOT start them."""
    global bus, power_agent, water_agent, telecom_agent
    bus           = EventBus()
    power_agent   = PowerAgent(bus,   power_json_path=POWER_JSON)
    water_agent   = WaterAgent(bus,   water_json_path=WATER_JSON,
                                      power_json_path=POWER_JSON)
    telecom_agent = TelecomAgent(bus, telecom_json_path=TELECOM_JSON,
                                      power_json_path=POWER_JSON,
                                      water_json_path=WATER_JSON)


_build_agents()  # initialise at import time so /state works before first start

# Connected WebSocket clients
clients: list[WebSocket] = []

async def broadcast_event(event: Event):
    """Send an event to all connected WebSocket clients."""
    if not clients:
        return
    
    data = event.to_dict()
    # Log small events (ticks) concisely, and full events for failures
    if data["event_type"] == "SIMULATION_TICK":
        logger.debug(f"Broadcasting TICK {data['tick']} to {len(clients)} clients")
    else:
        logger.info(f"Broadcasting {data['event_type']} ({data['node_id']}) to {len(clients)} clients")

    disconnected = []
    for client in clients:
        try:
            await client.send_json(data)
        except Exception as e:
            logger.error(f"Failed to send to client: {e}")
            disconnected.append(client)
    
    for client in disconnected:
        if client in clients:
            clients.remove(client)


async def _start_simulation():
    """Start bus + agents + simulation loop. Cancels old tasks first."""
    global _agent_tasks, _sim_task, power_agent, water_agent, telecom_agent

    # cancel previous tasks if any
    for t in _agent_tasks:
        t.cancel()
    if _sim_task and not _sim_task.done():
        _sim_task.cancel()
    _agent_tasks.clear()

    # Clear bus and re-instantiate agents for a clean state
    await bus.start()
    bus.clear_subscriptions()
    
    power_agent = PowerAgent(bus, POWER_JSON)
    water_agent = WaterAgent(bus, WATER_JSON, POWER_JSON)
    telecom_agent = TelecomAgent(bus, TELECOM_JSON, POWER_JSON, WATER_JSON)

    _agent_tasks.append(asyncio.create_task(power_agent.start()))
    _agent_tasks.append(asyncio.create_task(water_agent.start()))
    _agent_tasks.append(asyncio.create_task(telecom_agent.start()))

    # Subscribe to ALL events to bridge them to WebSockets
    orchestrator_queue = bus.subscribe_all("websocket_bridge")

    async def bridge_loop():
        while True:
            event = await orchestrator_queue.get()
            if event:
                await broadcast_event(event)

    _agent_tasks.append(asyncio.create_task(bridge_loop()))
    _sim_task = asyncio.create_task(simulation_loop())
    logger.info("Simulation server and bridge started")

async def simulation_loop():
    """Background task to run the simulation ticks."""
    tick = 0
    while True:
        logger.info(f"===== TICK {tick} =====")
        await bus.publish(sim_tick(tick))
        tick += 1
        await asyncio.sleep(5)  # 5 seconds per simulation tick for demo


@app.on_event("startup")
async def startup_event():
    """Start the event bus and agents on server startup."""
    await _start_simulation()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for the frontend."""
    await websocket.accept()
    clients.append(websocket)
    logger.info(f"Client connected. Total clients: {len(clients)}")
    
    try:
        while True:
            # Receive failure triggers from the frontend
            data = await websocket.receive_json()
            if data.get("type") == "FAIL_NODE":
                node_id = data.get("node_id")
                network_str = data.get("network", "power").lower()
                
                logger.info(f"Manual fail request: {node_id} in {network_str}")
                
                try:
                    net_enum = Network(network_str)
                    # Publish USER_FAIL_NODE to the bus
                    await bus.publish(user_fail_node(
                        net_enum,
                        node_id,
                        0 # current tick will be set by agents
                    ))
                except ValueError:
                    logger.error(f"Invalid network type: {network_str}")
                
    except WebSocketDisconnect:
        if websocket in clients:
            clients.remove(websocket)
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        if websocket in clients:
            clients.remove(websocket)


@app.get("/")
async def health_check():
    return {"status": "alive", "agents": ["power", "water", "telecom"]}

@app.api_route("/reset", methods=["GET", "POST"])
async def reset_simulation():
    """
    Tear down the current simulation and rebuild all agents from scratch.
    """
    global bus, power_agent, water_agent, telecom_agent

    logger.info("RESET REQUEST RECEIVED")

    # stop old bus
    if bus:
        try:
            await asyncio.wait_for(bus.stop(), timeout=2.0)
            bus.clear_subscriptions()
        except Exception as e:
            logger.error(f"Error stopping bus: {e}")

    # rebuild everything fresh
    _build_agents()
    await _start_simulation()

    logger.info("SIMULATION RESET COMPLETE - FRESH STATE READY")
    return {"status": "reset", "message": "Simulation restarted from clean state"}

@app.get("/state")
async def get_state():
    """Returns the current state of all agents."""
    return {
        "power": power_agent.get_all_states(),
        "water": water_agent.get_all_states(),
        "telecom": telecom_agent.get_all_states()
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

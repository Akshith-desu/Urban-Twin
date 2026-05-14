
import asyncio
import websockets
import json

async def trigger_failure():
    uri = "ws://localhost:8000/ws"
    try:
        async with websockets.connect(uri) as websocket:
            # 1. Trigger failure on Water Pumps
            pumps = ["WPS-OSM-01", "WPS-OSM-02", "WPS-OSM-03"]
            for pid in pumps:
                payload = {
                    "type": "FAIL_NODE",
                    "node_id": pid,
                    "network": "water"
                }
                print(f"Sending: {payload}")
                await websocket.send(json.dumps(payload))
            
            # 2. Listen for the response events
            print("Listening for cascade events (5 seconds)...")
            try:
                while True:
                    message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    data = json.loads(message)
                    if data.get("event_type") == "CASCADE_TRIGGERED":
                        meta = data.get("metadata", {})
                        print(f"CASCADE: {data.get('source_network')} -> {meta.get('target_network')} ({meta.get('target_node')})")
                    elif data.get("event_type") == "PUMP_STATION_FAIL":
                        print(f"FAIL: Pump {data.get('node_id')} failed")
            except asyncio.TimeoutError:
                print("Finished listening.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(trigger_failure())

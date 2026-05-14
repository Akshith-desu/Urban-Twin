import asyncio
import logging
import json
import geopandas as gpd
from pathlib import Path
from typing import List, Dict, Any, Optional

from event_schema import Event, EventType, Network, node_failed, cascade_triggered
from event_bus import EventBus

logger = logging.getLogger(__name__)

class BaseAgent:
    """
    Abstract base class for infrastructure sector agents.
    Handles event bus interaction, lifecycle, and common graph data.
    """

    def __init__(self, network: Network, bus: EventBus):
        self.network = network
        self.bus = bus
        self.name = f"{network.value}_agent"
        self.running = False
        
        # Graph data (nodes and edges belonging to this network)
        self.nodes_gdf: Optional[gpd.GeoDataFrame] = None
        self.edges_gdf: Optional[gpd.GeoDataFrame] = None
        
        # State: node_id -> { "health": float, "is_failed": bool, ... }
        self.state: Dict[str, Dict[str, Any]] = {}
        
        # Outgoing dependencies from this network
        self.dependencies: List[Dict[str, Any]] = []
        
        # Events scheduled for future ticks: [ { "target_tick": int, "event": Event }, ... ]
        self.pending_events: List[Dict[str, Any]] = []
        
        self.current_tick = 0

    async def start(self):
        """Initialise graph data and subscriptions."""
        self.load_graph()
        self.load_dependencies()
        self.init_state()
        
        # Subscribe to common events + specific ones in subclasses
        topics = self.get_subscribed_topics()
        self.bus.subscribe(self.name, topics)
        
        self.running = True
        logger.info(f"Agent {self.name} started and subscribed to {len(topics)} topics")

    def load_graph(self):
        """Load GeoPackage data for this network."""
        graphs_dir = Path(__file__).parent.parent / "graphs"
        node_path = graphs_dir / f"{self.network.value}_nodes_enriched.gpkg"
        if not node_path.exists():
            node_path = graphs_dir / f"{self.network.value}_nodes.gpkg"
            
        edge_path = graphs_dir / f"{self.network.value}_edges.gpkg"
        
        try:
            if node_path.exists():
                self.nodes_gdf = gpd.read_file(node_path)
            if edge_path.exists():
                self.edges_gdf = gpd.read_file(edge_path)
        except Exception as e:
            logger.error(f"Agent {self.name} failed to load graph data: {e}")

    def load_dependencies(self):
        """Load cross-network dependencies where this network is the provider."""
        dep_path = Path(__file__).parent.parent / "data" / "dependency_edges.json"
        if dep_path.exists():
            try:
                with open(dep_path) as f:
                    all_deps = json.load(f)
                    self.dependencies = [d for d in all_deps if d["from_network"] == self.network.value]
                logger.info(f"Agent {self.name}: Loaded {len(self.dependencies)} outgoing dependencies")
            except Exception as e:
                logger.error(f"Agent {self.name} failed to load dependencies: {e}")

    def init_state(self):
        """Initialise internal state from GeoDataFrame."""
        if self.nodes_gdf is not None:
            for _, row in self.nodes_gdf.iterrows():
                nid = str(row["node_id"])
                
                # Construct a descriptive name
                n_type = str(row.get("node_type", "Node")).replace("_", " ").title()
                n_name = row.get("name")
                
                if n_name and str(n_name) != nid:
                    display_name = f"{n_type} {n_name}"
                else:
                    display_name = f"{n_type} ({nid})"
                
                # Add sector-specific context
                if self.network == Network.POWER and row.get("substation_supplying"):
                    display_name += f" near {row['substation_supplying']}"
                elif self.network == Network.WATER and row.get("fill_source_node"):
                    display_name += f" (fed by {row['fill_source_node']})"
                
                self.state[nid] = {
                    "name": display_name,
                    "health": float(row.get("health", 1.0)),
                    "criticality": float(row.get("criticality", 1.0)),
                    "is_failed": False,
                    "metadata": {}
                }

    def get_subscribed_topics(self) -> List[EventType]:
        """Return list of EventTypes this agent cares about. Override in subclasses."""
        return [
            EventType.SIMULATION_TICK,
            EventType.SIMULATION_END,
            EventType.FLOOD_NODE,
            EventType.CASCADE_TRIGGERED,
            EventType.NODE_RECOVERED
        ]

    async def run(self):
        """Main event loop for the agent."""
        topics = self.get_subscribed_topics()
        async for event in self.bus.get_events(self.name, topics):
            if not self.running:
                break
            
            await self.process_event(event)

    async def process_event(self, event: Event):
        """Distribute events to specific handlers."""
        if event.event_type == EventType.SIMULATION_TICK:
            self.current_tick = event.tick
            await self.handle_tick(event.tick)
        
        elif event.event_type == EventType.SIMULATION_END:
            self.running = False
            logger.info(f"Agent {self.name} shutting down.")
            
        elif event.event_type == EventType.FLOOD_NODE:
            # Check if this node belongs to us
            nid = str(event.node_id)
            if nid in self.state:
                await self.handle_failure(event, node_id=nid)
        
        elif event.event_type == EventType.CASCADE_TRIGGERED:
            # Check if this cascade is targeting US
            meta = event.metadata or {}
            target_net = meta.get("target_network")
            target_node = meta.get("target_node")
            
            if target_net == self.network.value and target_node in self.state:
                logger.warning(f"Agent {self.name}: Received CASCADE failure for {target_node} from {event.source_network}")
                await self.handle_failure(event, node_id=target_node)
        
        elif event.event_type == EventType.NODE_RECOVERED:
            nid = str(event.node_id)
            if nid in self.state:
                await self.handle_recovery(event, node_id=nid)
        
        else:
            await self.handle_custom_event(event)

    async def handle_tick(self, tick: int):
        """Process pending events and perform periodic checks."""
        # 1. Process pending cascades/failures
        ready_events = [p for p in self.pending_events if p["target_tick"] <= tick]
        self.pending_events = [p for p in self.pending_events if p["target_tick"] > tick]
        
        for p in ready_events:
            logger.info(f"Agent {self.name}: Triggering delayed event {p['event'].event_type} for {p['event'].node_id}")
            await self.bus.publish(p["event"])

        # 2. Basic auto-recovery for degraded (not failed) nodes
        for nid, info in self.state.items():
            if not info["is_failed"] and info["health"] < 1.0:
                info["health"] = min(1.0, info["health"] + 0.05)

    async def handle_failure(self, event: Event, node_id: Optional[str] = None):
        """Handle a node failure (direct or cascaded)."""
        nid = node_id or event.node_id
        print(f"Agent {self.name} handling failure for node: {nid}")
        if nid in self.state:
            # Only fail if not already failed
            if not self.state[nid]["is_failed"]:
                self.state[nid]["health"] = 0.0
                self.state[nid]["is_failed"] = True
                logger.warning(f"Agent {self.name}: Node {nid} FAILED at tick {self.current_tick}")
                
                # Broadcast the failure so others can react
                failure_ev = node_failed(
                    network=self.network,
                    node_id=nid,
                    node_name=self.state[nid]["name"],
                    tick=self.current_tick,
                    cascade_depth=event.cascade_depth,
                    reason=event.event_type.value
                )
                await self.publish(failure_ev)
                
                # Propagate to dependent networks
                await self.propagate_failure(nid, event.cascade_depth)

    async def handle_recovery(self, event: Event):
        """Restore a failed node."""
        nid = event.node_id
        if nid in self.state and self.state[nid]["is_failed"]:
            self.state[nid]["health"] = 1.0
            self.state[nid]["is_failed"] = False
            logger.info(f"Agent {self.name}: Node {nid} RECOVERED at tick {self.current_tick}")
            # Emit recovery signal for cascading restoration if needed
            await self.publish(node_recovered(self.network, nid, self.current_tick, node_name=self.state[nid]["name"]))

    async def propagate_failure(self, node_id: str, current_depth: int):
        """Trigger cascades to other networks based on dependency rules."""
        import random
        affected_deps = [d for d in self.dependencies if str(d["from_node"]) == str(node_id)]
        
        for dep in affected_deps:
            # 1. Respect failure probability
            prob = dep.get("failure_probability", 1.0)
            if random.random() > prob:
                logger.info(f"Agent {self.name}: Cascade to {dep['to_node']} skipped (probability check)")
                continue

            # 2. Calculate target tick based on delay
            delay_ticks = int(dep.get("delay_minutes", 0))
            target_tick = self.current_tick + delay_ticks
            
            cascade_ev = cascade_triggered(
                source_network=self.network,
                source_node=node_id,
                source_name=self.state[node_id]["name"],
                target_network=Network(dep["to_network"]),
                target_node=str(dep["to_node"]),
                tick=target_tick,
                depth=current_depth + 1,
                dep_type=dep.get("dep_type", "unknown")
            )

            if delay_ticks > 0:
                logger.info(f"Agent {self.name}: Scheduling delayed cascade to {dep['to_node']} at tick {target_tick}")
                self.pending_events.append({"target_tick": target_tick, "event": cascade_ev})
            else:
                await self.publish(cascade_ev)

    async def handle_custom_event(self, event: Event):
        """Handle sector-specific events (CASCADE_TRIGGERED, etc.). Override in subclasses."""
        pass

    async def publish(self, event: Event):
        """Publish an event back to the bus."""
        event.tick = self.current_tick
        await self.bus.publish(event)

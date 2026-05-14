import { useEffect, useRef } from 'react';
import { useStore } from '../store/useStore';

export const useSimulation = () => {
  const { 
    phase, 
    currentTick, 
    setTick, 
    addEvent, 
    powerGraph, 
    waterGraph, 
    telecomGraph, 
    failNode,
    restoreNode,
    failedNodes,
    coordMap,
    addCascadeLink 
  } = useStore();
  
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    // Connect to Backend WebSocket (Automatic host detection)
    const host = window.location.hostname || 'localhost';
    const socket = new WebSocket(`ws://${host}:8000/ws`);
    socketRef.current = socket;

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
      console.log('%c[WebSocket] Received:', 'color: #8b5cf6', data.event_type, data.node_id || '');
      const state = useStore.getState();
      
      // Handle incoming events from Python backend
      if (data.event_type === 'SIMULATION_TICK') {
        state.setTick(data.tick);
      } else {
        state.addEvent(data);
        
        // Update failure state
        const failTypes = [
          'NODE_FAILED', 'USER_FAIL_NODE', 'CASCADE_TRIGGERED', 
          'SUBSTATION_FAILED', 'FEEDER_LINE_DROPPED', 'PUMP_STATION_FAIL',
          'CELL_TOWER_FAILED', 'CELL_TOWER_BATTERY', 'PRESSURE_DROP', 'TOWER_EMPTY'
        ];

        if (failTypes.includes(data.event_type)) {
          // If it's a bulk failure, fail all affected nodes
          if (data.affected_nodes && data.affected_nodes.length > 0) {
              data.affected_nodes.forEach((nid: string) => {
                  const targetNet = data.metadata?.target_network || data.source_network;
                  state.failNode(String(nid), targetNet);
              });
          }
          // The primary node mentioned in the event
          if (data.node_id) {
              state.failNode(String(data.node_id), data.source_network);
          }
        } else if (data.event_type === 'NODE_RECOVERED' || data.event_type === 'USER_RESTORE_NODE') {
          state.restoreNode(String(data.node_id));
        }

        // Create visual cascade links from backend events
        let sourceId: string | null = null;
        let sourceNet: string = String(data.source_network).toLowerCase();
        let targets: {id: string, net: string}[] = [];

        // Debug: Log the state of the coordinate map on first event
        if (state.coordMap.size > 0 && !window.hasOwnProperty('_coord_debug_done')) {
            console.log('%c[Diagnostic] CoordMap Sample:', 'color: #f59e0b', Array.from(state.coordMap.keys()).slice(0, 5));
            (window as any)._coord_debug_done = true;
        }

        if (data.event_type === 'CASCADE_TRIGGERED') {
            sourceId = String(data.node_id);
            const targetId = String(data.metadata?.target_node || data.affected_nodes?.[0]);
            const targetNet = String(data.metadata?.target_network || data.source_network).toLowerCase();
            if (targetId) targets.push({id: targetId, net: targetNet});
        } else if (data.event_type === 'SUBSTATION_FAILED' || data.event_type === 'FEEDER_LINE_DROPPED' || data.event_type === 'PUMP_STATION_FAIL' || data.event_type === 'NODE_FAILED') {
            sourceId = String(data.node_id);
            if (data.affected_nodes) {
                data.affected_nodes.forEach((tid: string) => {
                    targets.push({id: String(tid), net: sourceNet});
                });
            }
        } else if (data.event_type === 'TRANSFORMER_REROUTED') {
            // For reroutes, source is the substation we just lost
            sourceId = String(data.metadata?.from_sub || data.node_id);
            targets.push({id: String(data.node_id), net: 'power'});
        }

        if (sourceId && targets.length > 0) {
            targets.forEach(t => {
                const sKey = `${sourceNet}:${sourceId}`;
                const tKey = `${t.net}:${t.id}`;
                
                // Try ID first, then Name fallback (some events use names)
                const sourcePos = state.coordMap.get(sKey) || state.coordMap.get(`${sourceNet}:${data.metadata?.from_sub_name}`);
                const targetPos = state.coordMap.get(tKey) || state.coordMap.get(`${t.net}:${data.metadata?.transformer_name}`);
                
                if (sourcePos && targetPos) {
                    state.addCascadeLink({
                        sourcePosition: [sourcePos[1], sourcePos[0]],
                        targetPosition: [targetPos[1], targetPos[0]],
                        sourceNode: sourceId!,
                        targetNode: t.id,
                        sourceNetwork: sourceNet,
                        targetNetwork: t.net,
                        tick: data.tick,
                        depth: data.cascade_depth || 1
                    });
                } else {
                    console.warn(`[Cascade] Lookup failed for ${sKey} (${!!sourcePos}) -> ${tKey} (${!!targetPos})`);
                }
            });
        }
      }
    } catch (err) {
      console.error('[Simulation] Error processing message:', err, data);
    }
  };

    socket.onopen = () => console.log('%c[Simulation] WebSocket Connected', 'color: #10b981; font-weight: bold');
    socket.onerror = (err) => console.error('%c[Simulation] WebSocket Error:', 'color: #ef4444', err);
    socket.onclose = () => console.warn('%c[Simulation] WebSocket Disconnected', 'color: #f59e0b');

    return () => socket.close();
  }, []); // Stable: only connect once on mount

  // Method to fail a node via Backend
  const triggerFailure = (nodeId: string, network: string) => {
      console.log(`%c[Trigger] Failing ${network}:${nodeId}`, 'color: #3b82f6');
      if (socketRef.current?.readyState === WebSocket.OPEN) {
          socketRef.current.send(JSON.stringify({
              type: 'FAIL_NODE',
              node_id: nodeId,
              network: network
          }));
      } else {
          console.warn('[Trigger] Socket not open, falling back to local failure');
          state.failNode(nodeId, network);
      }
  };

  // Method to restore a node via Backend
  const triggerRestore = (nodeId: string, network: string) => {
      if (socketRef.current?.readyState === WebSocket.OPEN) {
          socketRef.current.send(JSON.stringify({
              type: 'RESTORE_NODE',
              node_id: nodeId,
              network: network
          }));
      }
  };

  useEffect(() => {
    if (phase !== 'running' || socketRef.current?.readyState === WebSocket.OPEN) return;

    const interval = setInterval(() => {
      setTick(currentTick + 1);
    }, 5000);

    return () => clearInterval(interval);
  }, [phase, currentTick, setTick, socketRef.current?.readyState]);

  // Cascade Propagation Logic (Local fallback)
  useEffect(() => {
    if (socketRef.current?.readyState === WebSocket.OPEN) return;
    if (failedNodes.size === 0) return;

    failedNodes.forEach(failedId => {
      // Power -> Water/Telecom propagation
      if (failedId.startsWith('substation') || failedId.startsWith('transformer')) {
        waterGraph?.nodes.forEach(wn => {
          if (wn.power_dependency === failedId) {
             if (!failedNodes.has(wn.node_id)) {
                setTimeout(() => {
                  failNode(wn.node_id, 'water');
                  createLocalCascadeLink(failedId, wn.node_id, 'power', 'water');
                }, 1000);
             }
          }
        });

        telecomGraph?.nodes.forEach(tn => {
          if (tn.power_dependency === failedId) {
             if (!failedNodes.has(String(tn.node_id))) {
                setTimeout(() => {
                  failNode(String(tn.node_id), 'telecom');
                  createLocalCascadeLink(failedId, String(tn.node_id), 'power', 'telecom');
                }, 2000);
             }
          }
        });
      }

      // Power Substation -> Transformers
      if (failedId.startsWith('substation')) {
        powerGraph?.edges.forEach(edge => {
          const fromNode = powerGraph.nodes.find(n => n.name === edge.from);
          const toNode = powerGraph.nodes.find(n => n.name === edge.to);
          
          if (fromNode?.node_id === failedId && edge.edge_type === 'primary_supply') {
            if (toNode && !failedNodes.has(toNode.node_id)) {
              setTimeout(() => {
                failNode(toNode.node_id, 'power');
                createLocalCascadeLink(failedId, toNode.node_id, 'power', 'power');
              }, 500);
            }
          }
        });
      }
    });
  }, [failedNodes, powerGraph, waterGraph, telecomGraph, failNode, socketRef.current?.readyState, coordMap]);

  const createLocalCascadeLink = (sourceId: string, targetId: string, sourceNet: string, targetNet: string) => {
    const sourcePos = coordMap.get(`${sourceNet}:${sourceId}`);
    const targetPos = coordMap.get(`${targetNet}:${targetId}`);

    if (sourcePos && targetPos) {
      addCascadeLink({
        sourcePosition: [sourcePos[1], sourcePos[0]],
        targetPosition: [targetPos[1], targetPos[0]],
        sourceNode: sourceId,
        targetNode: targetId,
        sourceNetwork: sourceNet as any,
        targetNetwork: targetNet as any,
        tick: currentTick,
        depth: 1
      });
    }
  };

  return { triggerFailure, triggerRestore };
};

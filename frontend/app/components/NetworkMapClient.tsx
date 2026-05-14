"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import { MapContainer, TileLayer, GeoJSON, useMap, LayersControl, Polyline } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { fetchEvents } from "../lib/api";
import { Activity, AlertTriangle, Info, Network, Settings2, Zap } from "lucide-react";

interface NetworkMapProps {
  networkData: any; // GeoJSON FeatureCollection
  focusNetwork?: string;
  showAllNetworks?: boolean;
}

const NETWORK_COLORS: Record<string, string> = {
  power: "#fbbf24",
  water: "#38bdf8",
  telecom: "#a855f7",
};

const MetricBar = ({ label, value, colorClass = "bg-blue-500", max = 1 }: { label: string, value: number, colorClass?: string, max?: number }) => (
  <div className="space-y-1">
    <div className="flex justify-between text-[10px] text-slate-400">
      <span className="capitalize">{label.replace(/_/g, ' ')}</span>
      <span className="font-bold text-white">{(value || 0).toFixed(2)}</span>
    </div>
    <div className="w-full h-1 bg-slate-800 rounded-full overflow-hidden">
      <div 
        className={`h-full ${colorClass} transition-all duration-500`}
        style={{ width: `${Math.min(100, (value / max) * 100)}%` }}
      />
    </div>
  </div>
);

const HealthHistory = ({ history }: { history: number[] }) => {
  if (!history || history.length === 0) return null;
  return (
    <div className="flex gap-0.5 h-4 items-end">
      {history.map((h, i) => (
        <div 
          key={i} 
          className={`flex-1 rounded-t-sm transition-all duration-300 ${h > 0.8 ? 'bg-emerald-500' : h > 0.4 ? 'bg-amber-500' : 'bg-rose-500'}`}
          style={{ height: `${h * 100}%`, minWidth: '4px' }}
        />
      ))}
    </div>
  );
};

const renderProviders = (providers: any) => {
  if (!providers) return null;
  const list = typeof providers === 'string' ? JSON.parse(providers) : providers;
  if (!Array.isArray(list)) return <div className="text-[10px] text-white font-mono">{String(providers)}</div>;
  
  return (
    <div className="space-y-2 mt-2">
      {list.map((p: any, i: number) => (
        <div key={i} className="text-[10px] bg-slate-900/60 p-2 rounded border border-slate-800/80">
          <div className="flex justify-between font-bold text-blue-300">
            <span>{p.operator} {p.technology}</span>
            <span>{p.frequency_mhz} MHz</span>
          </div>
          <div className="text-slate-500 mt-1 flex justify-between">
            <span>TX: {p.transmit_power_dbm}dBm</span>
            <span>Radius: {p.coverage_radius_m?.toFixed(0)}m</span>
          </div>
        </div>
      ))}
    </div>
  );
};

// Component to recenter map when data changes
function MapController({ data }: { data: any }) {
  const map = useMap();
  useEffect(() => {
    if (data && data.features && data.features.length > 0) {
      try {
        const geoJsonLayer = L.geoJSON(data);
        const bounds = geoJsonLayer.getBounds();
        if (bounds.isValid()) {
          map.fitBounds(bounds, { padding: [40, 40], animate: true, duration: 1.5 });
        }
      } catch (e) {
        console.error("Error setting bounds", e);
      }
    }
  }, [data, map]);
  return null;
}

export default function NetworkMapClient({ networkData, focusNetwork, showAllNetworks }: NetworkMapProps) {
  const mapRef = useRef<L.Map>(null);
  const [failedNodes, setFailedNodes] = useState<Set<string>>(new Set());
  const [updateCounter, setUpdateCounter] = useState(0);
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [mapMode, setMapMode] = useState<"dark" | "satellite">("satellite");
  const [dependencies, setDependencies] = useState<any[]>([]);
  const [allNodesMap, setAllNodesMap] = useState<Record<string, [number, number]>>({});

  useEffect(() => {
    // 1. Fetch historical failures
    fetchEvents().then(data => {
      if (data && data.events) {
        const failed = new Set<string>();
        data.events.forEach((ev: any) => {
          if (ev.event_type === "NODE_FAILED") {
            failed.add(ev.node_id);
          } else if (ev.event_type === "CASCADE_TRIGGERED") {
            const targetId = ev.metadata?.target_node;
            if (targetId) failed.add(targetId);
          }
        });
        setFailedNodes(failed);
      }
    });

    // 2. Fetch Dependencies and All Nodes for mapping
    fetch("http://localhost:8000/api/dependencies").then(r => r.json()).then(setDependencies);
    fetch("http://localhost:8000/api/graphs/combined/all").then(r => r.json()).then(data => {
      const coords: Record<string, [number, number]> = {};
      data.features.forEach((f: any) => {
        if (f.properties?._ftype === "node") {
          const nid = f.properties.node_id || f.properties.id;
          if (nid) coords[nid] = [f.geometry.coordinates[1], f.geometry.coordinates[0]];
        }
      });
      setAllNodesMap(coords);
    });

    let ws: WebSocket;
    let reconnectTimeout: NodeJS.Timeout;

    const connectWS = () => {
      ws = new WebSocket("ws://127.0.0.1:8000/ws/events");
      
      ws.onopen = () => {
        console.log("WebSocket connected successfully");
      };

      ws.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data);
          console.log("WebSocket Event:", data);
          if (data.type === "event" && data.data) {
            const ev = data.data;
            if (ev.event_type === "NODE_FAILED") {
               if (ev.node_id) {
                 const nid = String(ev.node_id);
                 console.log("Marking node as failed:", nid);
                 setFailedNodes(prev => new Set([...prev, nid]));
                 setUpdateCounter(c => c + 1);
               }
            } else if (ev.event_type === "CASCADE_TRIGGERED") {
               const targetId = ev.metadata?.target_node;
               if (targetId) {
                 setFailedNodes(prev => new Set([...prev, targetId]));
                 setUpdateCounter(c => c + 1);
               }
            } else if (ev.event_type === "NODE_RECOVERED") {
              if (ev.node_id) {
                 const nid = String(ev.node_id);
                 setFailedNodes(prev => {
                   const next = new Set(prev);
                   next.delete(nid);
                   return next;
                 });
                 setUpdateCounter(c => c + 1);
              }
            }
          }
        } catch (e) {
          console.error("Map WS error", e);
        }
      };

      ws.onclose = () => {
        console.log("WebSocket closed, attempting to reconnect in 2s...");
        reconnectTimeout = setTimeout(connectWS, 2000);
      };

      ws.onerror = (err) => {
        console.error("WebSocket error:", err);
      };
    };

    connectWS();
    return () => {
      clearTimeout(reconnectTimeout);
      if (ws) ws.close();
    };
  }, []);

  // Compute dependency arcs for the selected node
  const activeArcs = useMemo(() => {
    if (!selectedNode) return [];
    const nid = selectedNode.node_id || selectedNode.id;
    
    return dependencies.filter(d => String(d.from_node) === String(nid) || String(d.to_node) === String(nid))
      .map(d => {
        const from = allNodesMap[d.from_node];
        const to = allNodesMap[d.to_node];
        if (!from || !to) return null;
        return {
          id: d.dep_id,
          coords: [from, to],
          type: d.dep_type,
          isOut: String(d.from_node) === String(nid)
        };
      }).filter(Boolean);
  }, [selectedNode, dependencies, allNodesMap]);

  const getStyle = (feature: any) => {
    const net = feature.properties?._network || focusNetwork || "power";
    const color = NETWORK_COLORS[net] || "#888";
    const isEdge = feature.geometry.type === "LineString" || feature.geometry.type === "MultiLineString";
    
    const opacity = (showAllNetworks && focusNetwork && net !== focusNetwork) ? 0.15 : 0.85;

    if (isEdge) {
      return {
        color: color,
        weight: net === "power" ? 2.5 : (net === "telecom" ? 3.5 : 2),
        opacity: opacity,
        dashArray: net === "telecom" ? "5, 5" : undefined,
      };
    }
    
    return {};
  };

  const pointToLayer = (feature: any, latlng: L.LatLng) => {
    const nid = feature.properties?.node_id || feature.properties?.id || "";
    const isFailed = failedNodes.has(nid);
    const net = feature.properties?._network || focusNetwork || "power";
    const color = NETWORK_COLORS[net] || "#888";
    const opacity = (showAllNetworks && focusNetwork && net !== focusNetwork) ? 0.2 : 1.0;
    
    if (isFailed) {
      return L.marker(latlng, {
        icon: L.divIcon({
          className: "relative",
          html: `<div class="w-3 h-3 rounded-full pulse-node" style="background: #f43f5e; box-shadow: 0 0 12px #f43f5e;"></div>`,
          iconSize: [12, 12],
          iconAnchor: [6, 6]
        })
      });
    }
    
    const isSelected = selectedNode && (selectedNode.node_id === nid || selectedNode.id === nid);
    
    return L.circleMarker(latlng, {
      radius: isSelected ? 8 : 5,
      fillColor: color,
      color: isSelected ? "#fff" : "#fff",
      weight: isSelected ? 3 : 1.5,
      opacity: opacity,
      fillOpacity: opacity,
      className: "node-glow",
    });

  };

  const onEachFeature = (feature: any, layer: L.Layer) => {
    layer.on('click', (e) => {
      setSelectedNode(feature.properties);
      L.DomEvent.stopPropagation(e);
    });

    if (feature.properties) {
      const p = feature.properties;
      const tooltipTitle = p.name || `${(p.node_type || 'Asset').replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())} ${p.node_id || p.id}`;
      const tooltipContent = `
        <div class="glass p-2 border-0 rounded shadow-none text-[10px] font-sans">
          <strong class="text-white">${tooltipTitle}</strong>
          <div class="text-slate-400 mt-0.5 uppercase tracking-tighter">${p._network || 'Infrastructure'} | ${p.node_id || p.id}</div>
        </div>
      `;
      layer.bindTooltip(tooltipContent, { sticky: true, className: 'custom-tooltip' });
    }
  };

  return (
    <div className="relative w-full h-full bg-[#060910] overflow-hidden">
      {/* Map Header Overlay */}
      <div className="absolute top-6 left-6 z-[1000] flex items-center gap-4">
        <div className="glass-panel px-4 py-2 flex items-center gap-3">
          <Network className="text-blue-400 w-5 h-5" />
          <div>
            <div className="text-xs font-bold text-white uppercase tracking-widest">Kepler Interface</div>
            <div className="text-[10px] text-slate-400">Halasuru Digital Twin v2.0</div>
          </div>
        </div>
        
        <div className="glass-panel p-1 flex gap-1">
          <button 
            onClick={() => setMapMode("dark")}
            className={`px-3 py-1 text-[10px] uppercase font-bold rounded-lg transition-all ${mapMode === 'dark' ? 'bg-blue-500 text-white' : 'text-slate-500 hover:text-slate-300'}`}
          >
            Tactical
          </button>
          <button 
            onClick={() => setMapMode("satellite")}
            className={`px-3 py-1 text-[10px] uppercase font-bold rounded-lg transition-all ${mapMode === 'satellite' ? 'bg-blue-500 text-white' : 'text-slate-500 hover:text-slate-300'}`}
          >
            Satellite
          </button>
        </div>
      </div>

      {/* Selected Node Inspector */}
      {selectedNode && (
        <div className="absolute right-6 top-6 bottom-6 w-85 z-[1000] animate-in slide-in-from-right-8 duration-300">
          <div className="glass-panel h-full flex flex-col relative overflow-hidden">
            <div className="tech-corner tech-corner-tl" />
            <div className="tech-corner tech-corner-br" />
            
            <div className="p-6 border-b border-slate-800 bg-slate-900/20">
              <div className="flex justify-between items-start mb-4">
                <div className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-widest bg-slate-800 text-slate-400 border border-slate-700`}>
                  {selectedNode._network || 'Asset'}
                </div>
                <button 
                  onClick={() => setSelectedNode(null)}
                  className="text-slate-500 hover:text-white transition-colors"
                >
                  <Settings2 className="w-4 h-4" />
                </button>
              </div>
              <h3 className="text-xl font-bold text-white font-display mb-1 tracking-tight">
                {selectedNode.name || 
                  `${(selectedNode.node_type || 'Asset').replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())} ${selectedNode.node_id || selectedNode.id}`}
              </h3>
              <p className="text-[10px] text-slate-500 font-mono tracking-tighter">ID: {selectedNode.node_id || selectedNode.id}</p>
            </div>

            <div className="flex-1 overflow-y-auto p-6 space-y-8 custom-scrollbar">
              {/* Vitals Section */}
              <section>
                <h4 className="text-[10px] uppercase tracking-widest text-slate-500 font-bold mb-4 flex items-center gap-2">
                  <Activity className="w-3 h-3" /> Operational Vitals
                </h4>
                <div className="grid gap-4">
                  <div className="p-4 bg-slate-900/50 rounded-xl border border-slate-800/50 space-y-4">
                    <MetricBar label="System Health" value={selectedNode.health} colorClass={selectedNode.health > 0.8 ? "bg-emerald-500" : selectedNode.health > 0.4 ? "bg-amber-500" : "bg-rose-500"} />
                    {selectedNode.health_history && (
                      <div className="pt-2 border-t border-slate-800/50">
                        <div className="text-[9px] text-slate-500 uppercase mb-2">Health Log (Recent)</div>
                        <HealthHistory history={selectedNode.health_history} />
                      </div>
                    )}
                  </div>
                  
                  <div className="grid grid-cols-2 gap-3">
                    <div className="p-3 bg-slate-900/50 rounded-xl border border-slate-800/50">
                      <MetricBar label="Criticality" value={selectedNode.criticality || 0} colorClass="bg-blue-500" max={10} />
                    </div>
                    <div className="p-3 bg-slate-900/50 rounded-xl border border-slate-800/50">
                      <MetricBar label="Load" value={selectedNode.load_fraction || 0} colorClass="bg-indigo-500" />
                    </div>
                  </div>
                </div>
              </section>

              {/* Operational Actions */}
              <section>
                <h4 className="text-[10px] uppercase tracking-widest text-slate-500 font-bold mb-3 flex items-center gap-2">
                  <Zap className="w-3 h-3 text-amber-500" /> Operational Control
                </h4>
                <div className="flex gap-2">
                  <button 
                    onClick={async () => {
                      const nid = selectedNode.node_id || selectedNode.id;
                      console.log("Triggering failure for:", nid);
                      const resp = await fetch("http://localhost:8000/api/simulation/fail-node", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ node_id: nid })
                      });
                      if (resp.ok) {
                        alert(`Manual failure injected for ${nid}. Cascade initiated.`);
                      } else {
                        console.error("Failure trigger failed:", await resp.text());
                      }
                    }}
                    className="flex-1 py-2 bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/30 text-rose-500 rounded-lg text-[10px] font-bold uppercase transition-all"
                  >
                    Trigger Failure
                  </button>
                  <button 
                    onClick={async () => {
                      const nid = selectedNode.node_id || selectedNode.id;
                      console.log("Restoring node:", nid);
                      const resp = await fetch("http://localhost:8000/api/simulation/recover-node", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ node_id: nid })
                      });
                      if (resp.ok) {
                        alert(`Manual restoration initiated for ${nid}.`);
                      } else {
                        console.error("Restoration failed:", await resp.text());
                      }
                    }}
                    className="flex-1 py-2 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 text-emerald-500 rounded-lg text-[10px] font-bold uppercase transition-all"
                  >
                    Restore Node
                  </button>
                </div>
              </section>

              {/* Technical Property Grouping */}
              <section>
                <h4 className="text-[10px] uppercase tracking-widest text-slate-500 font-bold mb-3 flex items-center gap-2">
                  <Info className="w-3 h-3" /> Technical Dataset
                </h4>
                <div className="space-y-1">
                  {Object.entries(selectedNode)
                    .filter(([k]) => !k.startsWith('_') && !['health', 'criticality', 'load_fraction', 'health_history', 'providers', 'battery', 'name', 'node_id', 'id'].includes(k))
                    .map(([key, val]: [string, any]) => (
                      <div key={key} className="flex justify-between items-center py-2 border-b border-slate-800/50 text-[10px]">
                        <span className="text-slate-400 capitalize">{key.replace(/_/g, ' ')}</span>
                        <span className="text-white font-mono">{typeof val === 'number' ? val.toFixed(3) : String(val)}</span>
                      </div>
                    ))}
                </div>
              </section>

              {/* Complex Data Rendering */}
              {selectedNode.battery && (
                <section>
                  <h4 className="text-[10px] uppercase tracking-widest text-slate-500 font-bold mb-3">Battery Subsystem</h4>
                  <div className="p-3 bg-slate-900/50 rounded-xl border border-slate-800/50 space-y-3">
                    <MetricBar 
                      label="Remaining Charge" 
                      value={selectedNode.battery.remaining_kwh || 0} 
                      max={selectedNode.battery.capacity_kwh || 1} 
                      colorClass="bg-emerald-400"
                    />
                    <div className="flex justify-between text-[10px]">
                      <span className="text-slate-400">Backup Time</span>
                      <span className="text-white font-bold">{selectedNode.battery.backup_hours}h</span>
                    </div>
                  </div>
                </section>
              )}

              {selectedNode.providers && (
                <section>
                  <h4 className="text-[10px] uppercase tracking-widest text-slate-500 font-bold mb-3">Service Providers</h4>
                  {renderProviders(selectedNode.providers)}
                </section>
              )}

              {activeArcs.length > 0 && (
                <section>
                  <h4 className="text-[10px] uppercase tracking-widest text-slate-500 font-bold mb-3 flex items-center gap-2">
                    <Network className="w-3 h-3" /> Dependency Graph
                  </h4>
                  <div className="space-y-2">
                    {activeArcs.map((arc: any, i) => (
                      <div key={i} className="p-2 bg-slate-900/40 border border-slate-800/50 rounded-lg flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <div className={`w-1.5 h-1.5 rounded-full ${arc.isOut ? 'bg-blue-400' : 'bg-purple-400'}`} />
                          <span className="text-[10px] text-slate-300 font-mono">
                            {arc.isOut ? "Supply to:" : "Powered by:"}
                          </span>
                        </div>
                        <span className="text-[10px] font-bold text-white">
                          {arc.isOut ? dependencies.find(d => d.dep_id === arc.id)?.to_node : dependencies.find(d => d.dep_id === arc.id)?.from_node}
                        </span>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {failedNodes.has(selectedNode.node_id || selectedNode.id) && (
                <div className="p-4 bg-rose-500/10 border border-rose-500/30 rounded-xl flex items-center gap-3">
                  <AlertTriangle className="text-rose-500 w-5 h-5 shrink-0" />
                  <div>
                    <div className="text-xs font-bold text-rose-500 uppercase">Status: Failed</div>
                    <div className="text-[10px] text-rose-400/80">Cascading failure detected</div>
                  </div>
                </div>
              )}
            </div>
            
            <div className="p-6 bg-slate-900/80 border-t border-slate-800 flex gap-3">
              <button className="flex-1 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-bold transition-all hover:scale-[1.02] active:scale-[0.98]">
                Trace Impact
              </button>
              <button 
                onClick={() => window.open(`http://localhost:8000/api/export/kepler`, '_blank')}
                className="px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-bold transition-colors"
                title="Export to Kepler.gl"
              >
                <Settings2 className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="absolute left-6 bottom-12 z-[1000] glass-panel p-4 space-y-3">
        <h4 className="text-[10px] uppercase tracking-widest text-slate-500 font-bold mb-1">Infrastructure</h4>
        {Object.entries(NETWORK_COLORS).map(([net, color]) => (
          <div key={net} className="flex items-center gap-3">
            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: color, boxShadow: `0 0 10px ${color}66` }} />
            <span className="text-[10px] text-slate-300 uppercase font-medium">{net}</span>
          </div>
        ))}
        <div className="flex items-center gap-3 mt-2 pt-2 border-t border-slate-800">
          <div className="w-3 h-3 rounded-full bg-danger pulse-node shadow-[0_0_10px_#f43f5e]" />
          <span className="text-[10px] text-rose-400 uppercase font-bold tracking-tighter">System Failure</span>
        </div>
      </div>

      <div className="map-vignette pointer-events-none" />

      <MapContainer
        center={[12.9762, 77.6265]} // Halasuru center
        zoom={15}
        style={{ height: "100%", width: "100%", background: "#04060a" }}
        ref={mapRef}
        zoomControl={false}
        attributionControl={false}
      >

        {mapMode === "dark" ? (
          <TileLayer
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          />
        ) : (
          <TileLayer
            url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
            className="satellite-map"
          />
        )}

        {/* Dependency Arcs Layer */}
        {activeArcs.map((arc: any, i) => (
          <Polyline
            key={i}
            positions={arc.coords}
            pathOptions={{
              color: arc.isOut ? "#60a5fa" : "#c084fc",
              weight: 2,
              opacity: 0.6,
              dashArray: "10, 10",
              className: "dependency-arc flow-animate"
            }}
          />
        ))}
        
        {networkData && networkData.features && (
          <GeoJSON
            key={`${JSON.stringify(networkData.stats)}-${mapMode}-${updateCounter}`} 
            data={networkData}
            style={getStyle}
            pointToLayer={pointToLayer}
            onEachFeature={onEachFeature}
          />
        )}
        
        <MapController data={networkData} />
      </MapContainer>
    </div>
  );
}


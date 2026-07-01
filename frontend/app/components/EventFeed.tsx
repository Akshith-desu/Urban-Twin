"use client";

import { useEffect, useState, useRef } from "react";
import { AlertTriangle, ShieldAlert, CheckCircle, Info } from "lucide-react";
import { fetchEvents } from "../lib/api";

interface SimEvent {
  event_id: string;
  tick: number;
  event_type: string;
  source_network: string;
  node_id: string;
  node_name?: string;
  severity: number;
  metadata?: any;
}

export default function EventFeed() {
  const [events, setEvents] = useState<SimEvent[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    // 1. Fetch historical events
    fetchEvents().then(data => {
      if (data && data.events) {
        // Events are usually chronologically ordered in jsonl, 
        // we want latest first for the feed.
        setEvents(data.events.reverse().slice(0, 100));
      }
    });

    // 2. Connect to WebSocket
    const ws = new WebSocket("ws://127.0.0.1:8000/ws/events");
    wsRef.current = ws;

    ws.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data.type === "event" && data.data) {
          setEvents((prev) => [data.data, ...prev].slice(0, 100)); // Keep last 100
        }
      } catch (e) {
        console.error("WS parse error", e);
      }
    };

    return () => {
      ws.close();
    };
  }, []);

  const getIcon = (type: string, severity: number) => {
    if (type === "NODE_FAILED" || severity > 0.7) return <ShieldAlert className="w-4 h-4 text-danger" />;
    if (type === "NODE_DEGRADED" || severity > 0.3) return <AlertTriangle className="w-4 h-4 text-warning" />;
    if (type === "NODE_RECOVERED") return <CheckCircle className="w-4 h-4 text-success" />;
    return <Info className="w-4 h-4 text-blue-400" />;
  };

  const getNetworkColor = (net: string) => {
    const colors: Record<string, string> = {
      road: "text-road border-road bg-road/10",
      power: "text-power border-power bg-power/10",
      water: "text-water border-water bg-water/10",
      telecom: "text-telecom border-telecom bg-telecom/10",
      system: "text-gray-400 border-gray-600 bg-gray-800",
    };
    return colors[net] || colors.system;
  };

  return (
    <div className="flex flex-col h-full bg-[#0a0f1a]/80 backdrop-blur">
      <div className="p-4 border-b border-gray-800 bg-gray-900/50">
        <h3 className="font-semibold text-gray-200 flex items-center gap-2">
          <ActivityIcon className="w-4 h-4 text-green-400 animate-pulse" />
          Live Event Feed
        </h3>
        <p className="text-xs text-gray-400 mt-1">Streaming from Python EventBus</p>
      </div>
      
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {events.length === 0 ? (
          <div className="text-center text-sm text-gray-500 py-10">
            Waiting for simulation events...
            <br/><br/>
            Start a scenario from the Simulation panel.
          </div>
        ) : (
          events.map((ev, i) => (
            <div key={`${ev.event_id}-${i}`} className="text-sm p-3 rounded-lg border border-gray-800 bg-black/40 animate-in fade-in slide-in-from-right-4 duration-300">
              <div className="flex items-start justify-between mb-2">
                <div className="flex items-center gap-2">
                  {getIcon(ev.event_type, ev.severity)}
                  <span className="font-mono text-xs text-gray-400">Tick {ev.tick}</span>
                </div>
                <span className={`text-[10px] uppercase font-bold px-1.5 py-0.5 rounded border ${getNetworkColor(ev.source_network)}`}>
                  {ev.source_network}
                </span>
              </div>
              
              <div className="font-medium mb-1">{ev.event_type.replace(/_/g, ' ')}</div>
              <div className="text-xs text-gray-300">
                <span className="text-gray-500">Asset:</span> <span className="text-blue-300 font-bold">{ev.node_name || ev.node_id}</span>
                {ev.node_name && <div className="text-[9px] text-gray-500 font-mono mt-0.5 tracking-tighter">REF: {ev.node_id}</div>}
              </div>
              
              {ev.metadata && Object.keys(ev.metadata).length > 0 && (
                <div className="mt-2 text-[10px] font-mono text-gray-400 bg-gray-900 p-1.5 rounded">
                  {Object.entries(ev.metadata).map(([k, v]) => (
                    <div key={k}>{k}: {String(v)}</div>
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function ActivityIcon(props: any) {
  return (
    <svg {...props} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
    </svg>
  );
}

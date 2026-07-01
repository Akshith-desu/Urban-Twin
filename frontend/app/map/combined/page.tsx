"use client";

import { useEffect, useState } from "react";
import Map from "../../components/Map";
import { fetchCombined } from "../../lib/api";
import EventFeed from "../../components/EventFeed";
import { Layers } from "lucide-react";

export default function CombinedMapPage() {
  const [data, setData] = useState<any>(null);
  const [activeNetwork, setActiveNetwork] = useState<string>("all");

  useEffect(() => {
    fetchCombined().then(setData);
  }, []);

  return (
    <div className="h-full flex relative bg-[#060910]">
      <div className="flex-1 relative">
        {/* Network Filter Overlay - Minimalist version */}
        <div className="absolute top-24 left-6 z-[1000] animate-in fade-in slide-in-from-left-4 duration-500">
          <div className="glass-panel p-1 flex gap-1 shadow-2xl border-white/5">
            {["all", "road", "power", "water", "telecom"].map((net) => (
              <button 
                key={net}
                onClick={() => setActiveNetwork(net)} 
                className={`px-4 py-1.5 text-[10px] font-bold uppercase tracking-widest rounded-lg transition-all ${
                  activeNetwork === net 
                    ? "bg-white/10 text-white shadow-inner" 
                    : "text-slate-500 hover:text-slate-300 hover:bg-white/5"
                }`}
              >
                {net}
              </button>
            ))}
          </div>
        </div>

        <Map 
          networkData={data} 
          focusNetwork={activeNetwork === "all" ? undefined : activeNetwork} 
          showAllNetworks={activeNetwork !== "all"} 
        />
      </div>
      
      {/* Event Feed - Premium Sidebar */}
      <div className="w-80 border-l border-white/5 glass z-50 flex flex-col shadow-2xl relative">
        <div className="p-6 border-b border-white/5">
          <h2 className="text-lg font-bold text-white flex items-center gap-3 font-display">
            <Layers className="text-blue-400 w-5 h-5" /> Live Event Bus
          </h2>
          <p className="text-[10px] text-slate-500 uppercase tracking-widest mt-1 font-bold">Real-time Telemetry</p>
        </div>
        <div className="flex-1 overflow-hidden">
          <EventFeed />
        </div>
      </div>
    </div>
  );
}


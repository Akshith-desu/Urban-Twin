"use client";

import { useEffect, useState } from "react";
import Map from "../../components/Map";
import { fetchNetwork } from "../../lib/api";
import { Shuffle } from "lucide-react";

export default function RoadMapPage() {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    fetchNetwork("road").then(setData);
  }, []);

  return (
    <div className="h-full flex flex-col relative bg-[#060910]">
      <div className="absolute top-24 left-6 z-[1000] animate-in fade-in slide-in-from-left-4 duration-500">
        <div className="glass-panel px-4 py-3 border-white/5 shadow-2xl">
          <h2 className="text-lg font-bold text-road flex items-center gap-3 font-display">
            <Shuffle className="w-5 h-5" /> Road Infrastructure
          </h2>
          {data && (
            <p className="text-[10px] text-slate-500 mt-1 uppercase tracking-widest font-bold">
              {data.stats?.node_count} Nodes // {data.stats?.edge_count} Edges
            </p>
          )}
        </div>
      </div>
      <Map networkData={data} focusNetwork="road" />
    </div>
  );
}

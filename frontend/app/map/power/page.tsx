"use client";

import { useEffect, useState } from "react";
import Map from "../../components/Map";
import { fetchNetwork } from "../../lib/api";
import { Zap } from "lucide-react";

export default function PowerMapPage() {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    fetchNetwork("power").then(setData);
  }, []);

  return (
    <div className="h-full flex flex-col relative bg-[#060910]">
      <div className="absolute top-24 left-6 z-[1000] animate-in fade-in slide-in-from-left-4 duration-500">
        <div className="glass-panel px-4 py-3 border-white/5 shadow-2xl">
          <h2 className="text-lg font-bold text-power flex items-center gap-3 font-display">
            <Zap className="w-5 h-5" /> Electrical Grid
          </h2>
          {data && (
            <p className="text-[10px] text-slate-500 mt-1 uppercase tracking-widest font-bold">
              {data.stats?.node_count} Assets // {data.stats?.edge_count} Transmissions
            </p>
          )}
        </div>
      </div>
      <Map networkData={data} focusNetwork="power" />
    </div>
  );
}

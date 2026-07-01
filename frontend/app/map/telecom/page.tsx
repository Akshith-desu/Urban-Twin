"use client";

import { useEffect, useState } from "react";
import Map from "../../components/Map";
import { fetchNetwork } from "../../lib/api";

export default function TelecomMapPage() {
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    fetchNetwork("telecom").then(setData);
  }, []);

  return (
    <div className="h-full flex flex-col relative">
      <div className="absolute top-4 left-4 z-[400] glass-panel px-4 py-2">
        <h2 className="text-xl font-bold text-telecom flex items-center gap-2">
          Telecom Setup
        </h2>
        {data && (
          <p className="text-xs text-gray-400 mt-1">
            {data.stats?.node_count} nodes, {data.stats?.edge_count} edges
          </p>
        )}
      </div>
      <Map networkData={data} focusNetwork="telecom" />
    </div>
  );
}

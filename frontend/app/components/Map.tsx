"use client";

import dynamic from "next/dynamic";

// Dynamically import the map client with SSR disabled
const NetworkMapClient = dynamic(() => import("./NetworkMapClient"), {
  ssr: false,
  loading: () => <div className="flex-1 flex items-center justify-center glass text-gray-400">Loading map engine...</div>,
});

export default NetworkMapClient;

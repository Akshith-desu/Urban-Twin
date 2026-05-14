"use client";

import { useEffect, useState } from "react";
import { fetchStats } from "./lib/api";
import { Activity, Database, Network, Share2 } from "lucide-react";

export default function Dashboard() {
  const [stats, setStats] = useState<any>(null);

  useEffect(() => {
    fetchStats().then(setStats);
  }, []);

  return (
    <div className="p-8 max-w-6xl mx-auto w-full">
      <div className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight mb-2">Urban Twin Dashboard</h1>
        <p className="text-gray-400">Indiranagar / Halasuru infrastructure simulation environment.</p>
      </div>

      {!stats ? (
        <div className="animate-pulse flex space-x-4">
          <div className="flex-1 space-y-6 py-1">
            <div className="h-2 bg-gray-700 rounded w-1/4"></div>
            <div className="grid grid-cols-4 gap-4">
              <div className="h-24 bg-gray-800 rounded"></div>
              <div className="h-24 bg-gray-800 rounded"></div>
              <div className="h-24 bg-gray-800 rounded"></div>
              <div className="h-24 bg-gray-800 rounded"></div>
            </div>
          </div>
        </div>
      ) : (
        <div className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard icon={<Database className="text-blue-400"/>} title="Total Nodes" value={stats.total_nodes} />
            <StatCard icon={<Share2 className="text-green-400"/>} title="Total Edges" value={stats.total_edges} />
            <StatCard icon={<Network className="text-purple-400"/>} title="Cross-Network Deps" value={stats.dependencies} />
            <StatCard icon={<Activity className="text-red-400"/>} title="Simulation Status" value="Idle" />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-8">
            <div className="glass-panel p-6">
              <h2 className="text-lg font-semibold mb-4 border-b border-gray-800 pb-2">Network Inventory</h2>
              <div className="space-y-4">
                {Object.entries(stats.networks).map(([net, data]: [string, any]) => (
                  <div key={net} className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <div className={`w-3 h-3 rounded-full bg-${net}`}></div>
                      <span className="capitalize text-gray-300">{net}</span>
                    </div>
                    <div className="text-sm font-mono text-gray-400">
                      {data.nodes} nodes / {data.edges} edges
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="glass-panel p-6">
              <h2 className="text-lg font-semibold mb-4 border-b border-gray-800 pb-2">Quick Actions</h2>
              <div className="space-y-3">
                <a href="/map/combined" className="block w-full p-3 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm transition-colors text-center font-medium">
                  Open Combined Map View
                </a>
                <a href="/simulation" className="block w-full p-3 bg-blue-900/40 hover:bg-blue-800/60 text-blue-300 rounded-lg text-sm transition-colors text-center font-medium border border-blue-800/50">
                  Run Monte Carlo Analysis
                </a>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ title, value, icon }: { title: string, value: string | number, icon: any }) {
  return (
    <div className="glass-panel p-5 flex items-start gap-4">
      <div className="p-3 bg-gray-800/50 rounded-lg">
        {icon}
      </div>
      <div>
        <div className="text-sm text-gray-400 mb-1">{title}</div>
        <div className="text-2xl font-bold">{value}</div>
      </div>
    </div>
  );
}

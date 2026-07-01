"use client";
import { useState, useEffect } from "react";
import { 
  PlayCircle, 
  ShieldAlert, 
  Zap, 
  Loader2, 
  Activity, 
  Target, 
  BarChart3, 
  AlertTriangle,
  RefreshCw,
  Terminal,
  Cpu
} from "lucide-react";

export default function SimulationPage() {
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<any>(null);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    if (running) {
      const interval = setInterval(() => {
        setProgress(prev => (prev < 95 ? prev + Math.random() * 5 : prev));
      }, 200);
      return () => clearInterval(interval);
    } else {
      setProgress(0);
    }
  }, [running]);

  const runMonteCarlo = async () => {
    setRunning(true);
    setResults(null);
    try {
      const res = await fetch("http://localhost:8000/api/simulation/montecarlo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ n_runs: 100, scenario: "flood", target_network: "all" })
      });
      const data = await res.json();
      setResults(data);
    } catch (e) {
      console.error(e);
    } finally {
      setRunning(false);
    }
  };

  const injectFlood = async () => {
    try {
      await fetch("http://localhost:8000/api/simulation/flood", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat: 12.9762, lon: 77.6265, radius_m: 2000 })
      });
      // Use a more subtle toast or notification instead of alert in a real app
      alert("CRITICAL: Ulsoor Lake Flood event injected. Initiating cascade analysis.");
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="p-8 max-w-7xl mx-auto w-full flex flex-col h-full space-y-8">
      <header className="relative py-4">
        <div className="flex items-center gap-3 mb-2">
          <div className="p-2 bg-blue-500/20 rounded-lg border border-blue-500/30">
            <Cpu className="text-blue-400 w-6 h-6" />
          </div>
          <h1 className="text-4xl font-bold tracking-tight text-white font-display">
            Strategic Simulation Engine
          </h1>
        </div>
        <p className="text-slate-400 max-w-2xl text-lg">
          Advanced Monte Carlo analysis and scenario injection for infrastructure resilience testing.
        </p>
        <div className="absolute bottom-0 left-0 right-0 h-px bg-gradient-to-r from-blue-500/50 via-purple-500/50 to-transparent" />
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 flex-1">
        {/* Scenarios Panel */}
        <div className="lg:col-span-5 space-y-6">
          <div className="glass-panel p-6 relative overflow-hidden group">
            <div className="tech-corner tech-corner-tl" />
            <div className="tech-corner tech-corner-tr" />
            <div className="tech-corner tech-corner-bl" />
            <div className="tech-corner tech-corner-br" />
            
            <h2 className="text-xl font-bold mb-6 flex items-center gap-3 text-white">
              <ShieldAlert className="text-orange-400" /> Operational Scenarios
            </h2>

            <div className="space-y-4">
              <div className="p-5 border border-slate-700/50 rounded-xl bg-slate-800/20 hover:bg-slate-800/40 transition-all border-gradient">
                <div className="flex justify-between items-start mb-3">
                  <h3 className="font-bold text-blue-400 text-lg">Ulsoor Lake Overflow</h3>
                  <span className="text-[10px] uppercase tracking-widest bg-blue-500/20 text-blue-400 px-2 py-0.5 rounded border border-blue-500/30">Flood</span>
                </div>
                <p className="text-sm text-slate-400 mb-6 leading-relaxed">
                  Simulates a major hydraulic failure at the Halasuru basin. Affects all infrastructure within a 2km radius, triggering multi-tier cascading failures across power and telecom sectors.
                </p>
                <button 
                  onClick={injectFlood}
                  className="w-full group relative overflow-hidden px-6 py-3 bg-blue-600/20 hover:bg-blue-600/30 text-blue-400 rounded-lg border border-blue-500/30 transition-all flex items-center justify-center gap-2 font-semibold"
                >
                  <Activity className="w-4 h-4" />
                  Trigger Injection
                </button>
              </div>

              <div className="p-5 border border-slate-700/50 rounded-xl bg-slate-800/20 hover:bg-slate-800/40 transition-all border-gradient">
                <div className="flex justify-between items-start mb-3">
                  <h3 className="font-bold text-green-400 text-lg">Rapid Response Repair</h3>
                  <span className="text-[10px] uppercase tracking-widest bg-green-500/20 text-green-400 px-2 py-0.5 rounded border border-green-400/30">Recovery</span>
                </div>
                <p className="text-sm text-slate-400 mb-6 leading-relaxed">
                  Deploys rapid response teams to the Halasuru basin. Restores health to all failed infrastructure within a 2km radius of the lake center.
                </p>
                <button 
                  onClick={async () => {
                    await fetch("http://localhost:8000/api/simulation/recover", {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ lat: 12.9762, lon: 77.6265, radius_m: 2000 })
                    });
                    alert("RECOVERY: Response teams deployed. Monitoring system restoration.");
                  }}
                  className="w-full group relative overflow-hidden px-6 py-3 bg-green-600/20 hover:bg-green-600/30 text-green-400 rounded-lg border border-green-500/30 transition-all flex items-center justify-center gap-2 font-semibold"
                >
                  <RefreshCw className="w-4 h-4" />
                  Initiate Recovery
                </button>
              </div>
              
              <div className="p-5 border border-slate-800/50 rounded-xl bg-slate-900/40 opacity-40 grayscale pointer-events-none">
                <h3 className="font-bold text-slate-500 text-lg">Grid Power Outage</h3>
                <p className="text-xs text-slate-600 mt-2 italic">Module under development...</p>
              </div>
            </div>
          </div>

          <div className="glass-panel p-6 relative">
            <h2 className="text-lg font-bold mb-4 flex items-center gap-2 text-white">
              <Terminal className="text-slate-500 w-4 h-4" /> System Health
            </h2>
            <div className="grid grid-cols-2 gap-4 text-center">
              <div className="p-3 bg-slate-900/50 rounded-lg border border-slate-800">
                <div className="text-xs text-slate-500 uppercase mb-1">Agents Active</div>
                <div className="text-xl font-mono text-green-400">03</div>
              </div>
              <div className="p-3 bg-slate-900/50 rounded-lg border border-slate-800">
                <div className="text-xs text-slate-500 uppercase mb-1">Bus Latency</div>
                <div className="text-xl font-mono text-blue-400">12ms</div>
              </div>
            </div>
          </div>
        </div>

        {/* Monte Carlo Panel */}
        <div className="lg:col-span-7 flex flex-col">
          <div className="glass-panel p-8 flex-1 flex flex-col relative">
            <div className="tech-corner tech-corner-tl opacity-50" />
            <div className="tech-corner tech-corner-tr opacity-50" />
            
            <div className="flex justify-between items-start mb-8">
              <div>
                <h2 className="text-2xl font-bold flex items-center gap-3 text-white">
                  <BarChart3 className="text-purple-400" /> Statistical Risk Analysis
                </h2>
                <p className="text-slate-400 mt-1">
                  Computational vulnerability mapping via random permutation sets.
                </p>
              </div>
              {results && (
                <button 
                  onClick={() => setResults(null)}
                  className="p-2 hover:bg-slate-800 rounded-lg text-slate-500 transition-colors"
                >
                  <RefreshCw className="w-5 h-5" />
                </button>
              )}
            </div>

            {!results ? (
              <div className="flex-1 flex flex-col items-center justify-center border-2 border-dashed border-slate-800 rounded-2xl bg-slate-900/20 group hover:border-slate-700 transition-colors">
                {running ? (
                  <div className="text-center space-y-6 w-full max-w-xs px-8">
                    <div className="relative">
                      <div className="absolute inset-0 bg-purple-500/20 blur-3xl rounded-full" />
                      <Loader2 className="w-16 h-16 text-purple-500 animate-spin mx-auto relative z-10" />
                    </div>
                    <div>
                      <div className="text-white font-semibold mb-2">Simulating Iterations...</div>
                      <div className="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
                        <div 
                          className="h-full bg-gradient-to-r from-purple-600 to-blue-500 transition-all duration-300"
                          style={{ width: `${progress}%` }}
                        />
                      </div>
                      <div className="text-[10px] text-slate-500 mt-2 uppercase tracking-tighter">N=100 Monte Carlo Parallel Processing</div>
                    </div>
                  </div>
                ) : (
                  <div className="text-center p-8">
                    <div className="w-16 h-16 bg-slate-800 rounded-2xl flex items-center justify-center mx-auto mb-6 group-hover:scale-110 transition-transform shadow-xl">
                      <Target className="text-slate-500 group-hover:text-purple-400 transition-colors" />
                    </div>
                    <h3 className="text-white font-bold text-xl mb-3">No Analysis Active</h3>
                    <p className="text-slate-500 mb-8 max-w-sm">
                      Initialize a 100-run simulation to identify critical vulnerability hotspots across the urban infrastructure.
                    </p>
                    <button 
                      onClick={runMonteCarlo}
                      className="px-8 py-4 bg-purple-600 hover:bg-purple-500 text-white rounded-xl font-bold transition-all shadow-lg shadow-purple-500/20 flex items-center gap-3 mx-auto"
                    >
                      <PlayCircle className="w-5 h-5" />
                      Run Vulnerability Map
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex-1 flex flex-col animate-in fade-in slide-in-from-bottom-4 duration-500">
                <div className="flex justify-between items-center mb-6">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="text-rose-500 w-5 h-5" />
                    <h3 className="font-bold text-white text-lg">Top Criticality Nodes</h3>
                  </div>
                  <div className="flex items-center gap-2 text-xs">
                    <span className="flex h-2 w-2 rounded-full bg-green-500" />
                    <span className="text-slate-500">Analysis Complete</span>
                  </div>
                </div>

                <div className="space-y-3 overflow-y-auto max-h-[420px] pr-4 custom-scrollbar">
                  {results.most_vulnerable?.map((node: any, i: number) => (
                    <div key={i} className="group p-4 bg-slate-800/30 hover:bg-slate-800/60 rounded-xl border border-slate-700/50 transition-all flex items-center justify-between">
                      <div className="flex items-center gap-4">
                        <div className="w-10 h-10 bg-slate-900 rounded-lg flex items-center justify-center border border-slate-700">
                          <div className={`w-2 h-2 rounded-full ${
                            node.network === 'power' ? 'bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.5)]' :
                            node.network === 'water' ? 'bg-blue-400 shadow-[0_0_8px_rgba(56,189,248,0.5)]' :
                            'bg-purple-400 shadow-[0_0_8px_rgba(168,85,247,0.5)]'
                          }`} />
                        </div>
                        <div>
                          <div className="text-white font-bold">
                            {node.name || `${(node.node_type || 'Asset').replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase())} ${node.node_id}`}
                          </div>
                          <div className="font-mono text-[10px] text-slate-500 mt-0.5 tracking-tighter uppercase">{node.network} Network | ID: {node.node_id}</div>
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="text-2xl font-bold text-rose-500">
                          {(node.failure_probability * 100).toFixed(1)}%
                        </div>
                        <div className="w-24 h-1 bg-slate-900 rounded-full mt-1 overflow-hidden">
                          <div 
                            className="h-full bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.4)]"
                            style={{ width: `${node.failure_probability * 100}%` }}
                          />
                        </div>
                        <div className="text-[8px] text-slate-600 mt-1 uppercase tracking-tighter">Vulnerability Index</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}


"use client";
import { useEffect, useState } from "react";
import {
  BarChart3, PlayCircle, Loader2, AlertTriangle, Zap,
  Droplet, Wifi, RefreshCw, ChevronDown, ChevronUp, Target
} from "lucide-react";

interface NodeOption { id: string; name: string; type: string; }
interface Results {
  runs: number;
  risk: Record<string, number>;
  averages: Record<string, number>;
  vulnerable: {
    transformers: { name: string; pct: number }[];
    pumps:        { name: string; pct: number }[];
    towers:       { name: string; pct: number }[];
  };
}

const NETWORK_ICON: Record<string, any> = {
  power: Zap, water: Droplet, telecom: Wifi
};
const NETWORK_COLOR: Record<string, string> = {
  power: "text-yellow-400", water: "text-blue-400", telecom: "text-purple-400"
};
const TYPE_ORDER = ["substation","transformer","pump_station","water_tower","pipe_junction","tower"];

function RiskBar({ label, pct }: { label: string; pct: number }) {
  const color = pct >= 70 ? "bg-rose-500" : pct >= 30 ? "bg-amber-500" : "bg-emerald-500";
  const textColor = pct >= 70 ? "text-rose-400" : pct >= 30 ? "text-amber-400" : "text-emerald-400";
  const badge = pct >= 70 ? "HIGH" : pct >= 30 ? "MED" : "LOW";
  return (
    <div className="flex items-center gap-3 py-2 border-b border-slate-800/50">
      <div className="flex-1 min-w-0">
        <div className="text-xs text-slate-300 truncate">{label}</div>
        <div className="mt-1 h-1.5 bg-slate-800 rounded-full overflow-hidden">
          <div className={`h-full ${color} transition-all duration-700`} style={{ width: `${pct}%` }} />
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <span className="text-sm font-bold text-white font-mono w-12 text-right">{pct}%</span>
        <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border ${textColor} border-current`}>{badge}</span>
      </div>
    </div>
  );
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="p-3 bg-slate-900/50 rounded-xl border border-slate-800/50">
      <div className="text-[10px] text-slate-500 uppercase tracking-widest mb-1">{label}</div>
      <div className="text-xl font-bold text-white font-mono">{value}</div>
      {sub && <div className="text-[9px] text-slate-600 mt-0.5">{sub}</div>}
    </div>
  );
}

export default function MonteCarloPage() {
  const [nodes, setNodes]           = useState<Record<string, NodeOption[]>>({});
  const [selected, setSelected]     = useState<string[]>([]);
  const [runs, setRuns]             = useState(100);
  const [ticks, setTicks]           = useState(60);
  const [flood, setFlood]           = useState(false);
  const [noCascade, setNoCascade]   = useState(false);
  const [failAll, setFailAll]       = useState(false);
  const [loading, setLoading]       = useState(false);
  const [results, setResults]       = useState<Results | null>(null);
  const [error, setError]           = useState<string | null>(null);
  const [search, setSearch]         = useState("");
  const [expanded, setExpanded]     = useState<Record<string, boolean>>({ power: true });
  const [progress, setProgress]     = useState(0);

  useEffect(() => {
    fetch("http://localhost:8000/api/montecarlo/nodes")
      .then(r => r.json()).then(setNodes).catch(console.error);
  }, []);

  useEffect(() => {
    if (!loading) { setProgress(0); return; }
    const est = runs * 0.012 * 1000;
    const step = 100 / (est / 200);
    const iv = setInterval(() => setProgress(p => Math.min(p + step * (0.5 + Math.random()), 94)), 200);
    return () => clearInterval(iv);
  }, [loading, runs]);

  const toggle = (id: string) =>
    setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);

  const toggleAll = (net: string) => {
    const ids = (nodes[net] || []).map(n => n.id);
    const allIn = ids.every(id => selected.includes(id));
    setSelected(s => allIn ? s.filter(id => !ids.includes(id)) : [...new Set([...s, ...ids])]);
  };

  const run = async () => {
    setLoading(true); setResults(null); setError(null);
    try {
      const body: any = { runs, ticks, flood, no_cascade: noCascade, fail_all_substations: failAll };
      if (!failAll && selected.length > 0) {
        // Resolve IDs back to names for the MC engine
        const nameMap: Record<string, string> = {};
        Object.values(nodes).flat().forEach(n => { nameMap[n.id] = n.name; });
        body.fail = selected.map(id => nameMap[id] || id);
      }
      const res = await fetch("http://localhost:8000/api/montecarlo/run", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Run failed"); }
      const data = await res.json();
      setProgress(100);
      setTimeout(() => setResults(data), 300);
    } catch (e: any) {
      setError(e.message || "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const allNodes = Object.values(nodes).flat();
  const filtered = search
    ? allNodes.filter(n => n.name.toLowerCase().includes(search.toLowerCase()) || n.type.toLowerCase().includes(search.toLowerCase()))
    : null;

  const selectedNames = selected.map(id => allNodes.find(n => n.id === id)?.name || id);

  return (
    <div className="h-full flex flex-col bg-[#06090f] overflow-hidden">
      {/* Header */}
      <div className="px-8 py-5 border-b border-white/5 shrink-0">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-purple-500/20 rounded-lg border border-purple-500/30">
            <BarChart3 className="text-purple-400 w-5 h-5" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-white">Monte Carlo Risk Engine</h1>
            <p className="text-xs text-slate-500 mt-0.5">Statistical vulnerability analysis across infrastructure networks</p>
          </div>
        </div>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* ── LEFT: Config ── */}
        <div className="w-80 border-r border-white/5 flex flex-col overflow-y-auto">
          <div className="p-5 space-y-5">

            {/* Node Selector */}
            <section>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-xs font-bold uppercase tracking-widest text-slate-400">Failure Targets</h3>
                {selected.length > 0 && !failAll && (
                  <button onClick={() => setSelected([])} className="text-[10px] text-slate-500 hover:text-slate-300">Clear ({selected.length})</button>
                )}
              </div>

              {/* Fail All toggle */}
              <label className="flex items-center justify-between p-3 mb-3 bg-rose-500/10 border border-rose-500/20 rounded-lg cursor-pointer">
                <div>
                  <div className="text-xs font-bold text-rose-400">Fail All Substations</div>
                  <div className="text-[10px] text-slate-500 mt-0.5">Worst-case scenario</div>
                </div>
                <div onClick={() => setFailAll(f => !f)}
                  className={`w-10 h-5 rounded-full transition-colors relative ${failAll ? "bg-rose-500" : "bg-slate-700"}`}>
                  <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-all ${failAll ? "left-5" : "left-0.5"}`} />
                </div>
              </label>

              {!failAll && (
                <>
                  <input
                    type="text" placeholder="Search nodes..."
                    value={search} onChange={e => setSearch(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white placeholder-slate-600 mb-3 focus:outline-none focus:border-slate-600"
                  />

                  {search && filtered ? (
                    <div className="space-y-1 max-h-48 overflow-y-auto">
                      {filtered.map(n => (
                        <label key={n.id} className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-slate-800/50 cursor-pointer">
                          <input type="checkbox" checked={selected.includes(n.id)} onChange={() => toggle(n.id)}
                            className="accent-purple-500" />
                          <span className="text-xs text-slate-300 flex-1 truncate">{n.name}</span>
                          <span className="text-[9px] text-slate-600">{n.type}</span>
                        </label>
                      ))}
                      {filtered.length === 0 && <p className="text-xs text-slate-600 px-2">No matches</p>}
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {["power", "water", "telecom"].map(net => {
                        const Icon = NETWORK_ICON[net];
                        const netNodes = (nodes[net] || []);
                        const byType = netNodes.reduce((acc, n) => {
                          (acc[n.type] = acc[n.type] || []).push(n); return acc;
                        }, {} as Record<string, NodeOption[]>);
                        const isOpen = expanded[net];
                        const netSelected = netNodes.filter(n => selected.includes(n.id)).length;
                        return (
                          <div key={net} className="border border-slate-800/50 rounded-xl overflow-hidden">
                            <button onClick={() => setExpanded(e => ({ ...e, [net]: !isOpen }))}
                              className="w-full flex items-center gap-2 px-3 py-2.5 bg-slate-900/50 hover:bg-slate-900">
                              <Icon className={`w-3.5 h-3.5 ${NETWORK_COLOR[net]}`} />
                              <span className="text-xs font-bold text-slate-300 capitalize flex-1 text-left">{net}</span>
                              {netSelected > 0 && <span className="text-[9px] bg-purple-500/20 text-purple-400 px-1.5 py-0.5 rounded">{netSelected}</span>}
                              {isOpen ? <ChevronUp className="w-3 h-3 text-slate-600" /> : <ChevronDown className="w-3 h-3 text-slate-600" />}
                            </button>
                            {isOpen && (
                              <div className="p-2 space-y-0.5 max-h-48 overflow-y-auto">
                                <button onClick={() => toggleAll(net)}
                                  className="w-full text-left text-[10px] text-slate-500 hover:text-slate-300 px-2 py-1 mb-1">
                                  {netNodes.every(n => selected.includes(n.id)) ? "Deselect all" : "Select all"}
                                </button>
                                {TYPE_ORDER.filter(t => byType[t]).map(t => (
                                  <div key={t}>
                                    <div className="text-[9px] uppercase tracking-widest text-slate-600 px-2 py-1">{t.replace(/_/g," ")}</div>
                                    {byType[t].map(n => (
                                      <label key={n.id} className="flex items-center gap-2 px-2 py-1 rounded hover:bg-slate-800/40 cursor-pointer">
                                        <input type="checkbox" checked={selected.includes(n.id)} onChange={() => toggle(n.id)}
                                          className="accent-purple-500" />
                                        <span className="text-xs text-slate-300 truncate">{n.name}</span>
                                      </label>
                                    ))}
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </>
              )}
            </section>

            {/* Simulation Config */}
            <section className="border-t border-slate-800/50 pt-5">
              <h3 className="text-xs font-bold uppercase tracking-widest text-slate-400 mb-4">Configuration</h3>
              <div className="space-y-4">
                <div>
                  <div className="flex justify-between text-xs mb-2">
                    <span className="text-slate-400">Simulation Runs</span>
                    <span className="font-bold text-white font-mono">{runs}</span>
                  </div>
                  <input type="range" min="10" max="500" step="10" value={runs} onChange={e => setRuns(+e.target.value)}
                    className="w-full accent-purple-500" />
                  <div className="flex justify-between text-[9px] text-slate-600 mt-1"><span>10</span><span>500</span></div>
                </div>
                <div>
                  <div className="flex justify-between text-xs mb-2">
                    <span className="text-slate-400">Ticks per Run</span>
                    <span className="font-bold text-white font-mono">{ticks} <span className="text-slate-500 font-normal">({ticks * 5} min sim)</span></span>
                  </div>
                  <input type="range" min="10" max="120" step="5" value={ticks} onChange={e => setTicks(+e.target.value)}
                    className="w-full accent-purple-500" />
                  <div className="flex justify-between text-[9px] text-slate-600 mt-1"><span>10</span><span>120</span></div>
                </div>
              </div>

              <div className="mt-4 space-y-2">
                {[
                  { label: "Inject flood at tick 5", sub: "Pipe burst probability active", val: flood, set: setFlood, color: "blue" },
                  { label: "Disable cascade forcing", sub: "Pure baseline mode", val: noCascade, set: setNoCascade, color: "slate" },
                ].map(({ label, sub, val, set, color }) => (
                  <label key={label} className="flex items-center justify-between p-3 bg-slate-900/40 border border-slate-800/50 rounded-lg cursor-pointer">
                    <div>
                      <div className="text-xs text-slate-300">{label}</div>
                      <div className="text-[9px] text-slate-600 mt-0.5">{sub}</div>
                    </div>
                    <div onClick={() => set((v: boolean) => !v)}
                      className={`w-9 h-4.5 rounded-full transition-colors relative ${val ? `bg-${color}-500` : "bg-slate-700"}`}
                      style={{ minWidth: 36, height: 20 }}>
                      <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-all ${val ? "left-5" : "left-0.5"}`} />
                    </div>
                  </label>
                ))}
              </div>
            </section>

            {/* Run Button */}
            <button onClick={run} disabled={loading || (!failAll && selected.length === 0)}
              className="w-full py-3.5 bg-purple-600 hover:bg-purple-500 disabled:bg-slate-800 disabled:text-slate-600 text-white rounded-xl font-bold transition-all flex items-center justify-center gap-2 shadow-lg shadow-purple-500/20">
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <PlayCircle className="w-4 h-4" />}
              {loading ? `Running ${runs} simulations...` : "Run Analysis"}
            </button>
            {!failAll && selected.length === 0 && (
              <p className="text-[10px] text-slate-600 text-center -mt-3">Select at least one node to fail</p>
            )}
          </div>
        </div>

        {/* ── RIGHT: Results ── */}
        <div className="flex-1 overflow-y-auto p-6">
          {loading && (
            <div className="flex flex-col items-center justify-center h-full gap-6">
              <div className="relative">
                <div className="absolute inset-0 bg-purple-500/20 blur-3xl rounded-full" />
                <Loader2 className="w-16 h-16 text-purple-500 animate-spin relative z-10" />
              </div>
              <div className="text-center w-64">
                <div className="text-white font-semibold mb-3">Running {runs} simulations...</div>
                <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
                  <div className="h-full bg-gradient-to-r from-purple-600 to-blue-500 transition-all duration-300 rounded-full"
                    style={{ width: `${progress}%` }} />
                </div>
                <div className="text-xs text-slate-500 mt-2">{progress.toFixed(0)}% complete — est. {Math.round(runs * 0.012)}s</div>
              </div>
            </div>
          )}

          {error && (
            <div className="flex items-center gap-3 p-4 bg-rose-500/10 border border-rose-500/30 rounded-xl">
              <AlertTriangle className="text-rose-500 w-5 h-5 shrink-0" />
              <div>
                <div className="text-sm font-bold text-rose-400">Run Failed</div>
                <div className="text-xs text-rose-400/70 mt-0.5">{error}</div>
              </div>
            </div>
          )}

          {!loading && !results && !error && (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <div className="w-16 h-16 bg-slate-800/80 rounded-2xl flex items-center justify-center mb-5">
                <Target className="text-slate-500 w-8 h-8" />
              </div>
              <h2 className="text-xl font-bold text-white mb-2">No Analysis Yet</h2>
              <p className="text-slate-500 text-sm max-w-sm">Select failure targets on the left, configure your run parameters, then click Run Analysis.</p>
            </div>
          )}

          {results && !loading && (
            <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
              {/* Summary header */}
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-xl font-bold text-white">Analysis Complete</h2>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {results.runs} runs · {ticks} ticks/run · targets: {failAll ? "All Substations" : selectedNames.slice(0,3).join(", ")}{selectedNames.length > 3 ? ` +${selectedNames.length-3} more` : ""}
                  </p>
                </div>
                <button onClick={() => setResults(null)}
                  className="p-2 hover:bg-slate-800 rounded-lg text-slate-500 hover:text-white transition-colors">
                  <RefreshCw className="w-4 h-4" />
                </button>
              </div>

              {/* Key stats grid */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <StatCard label="Avg Transformers Failed" value={results.averages.tx_failed.toFixed(1)} />
                <StatCard label="Avg Pumps Failed" value={results.averages.pumps_failed.toFixed(1)} />
                <StatCard label="Avg Cascade Events" value={results.averages.cascade_events.toFixed(0)} />
                <StatCard label="Avg Min Water Pressure" value={results.averages.min_pressure.toFixed(3)} sub="0=critical 1=normal" />
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Risk probabilities */}
                <div className="bg-slate-900/30 border border-slate-800/50 rounded-2xl p-5">
                  <h3 className="text-sm font-bold text-white mb-4 flex items-center gap-2">
                    <AlertTriangle className="w-4 h-4 text-amber-500" /> Risk Probabilities
                  </h3>
                  <div className="space-y-0.5">
                    {Object.entries(results.risk).map(([label, pct]) => (
                      <RiskBar key={label} label={label} pct={pct} />
                    ))}
                  </div>
                </div>

                {/* Most vulnerable nodes */}
                <div className="space-y-4">
                  {[
                    { title: "Most Vulnerable Transformers", data: results.vulnerable.transformers, color: "text-yellow-400", bg: "bg-yellow-500/10 border-yellow-500/20" },
                    { title: "Most Vulnerable Pumps",        data: results.vulnerable.pumps,        color: "text-blue-400",   bg: "bg-blue-500/10 border-blue-500/20" },
                    { title: "Most Vulnerable Towers",       data: results.vulnerable.towers,        color: "text-purple-400", bg: "bg-purple-500/10 border-purple-500/20" },
                  ].map(({ title, data, color, bg }) => data.length > 0 && (
                    <div key={title} className={`border rounded-xl p-4 ${bg}`}>
                      <h4 className={`text-xs font-bold uppercase tracking-widest mb-3 ${color}`}>{title}</h4>
                      <div className="space-y-2">
                        {data.map((n, i) => (
                          <div key={i} className="flex items-center gap-3">
                            <span className="text-[10px] text-slate-500 w-4">{i+1}.</span>
                            <span className="text-xs text-slate-300 flex-1 truncate">{n.name}</span>
                            <div className="flex items-center gap-2">
                              <div className="w-16 h-1 bg-slate-800 rounded-full overflow-hidden">
                                <div className={`h-full ${n.pct >= 70 ? "bg-rose-500" : n.pct >= 30 ? "bg-amber-500" : "bg-emerald-500"}`}
                                  style={{ width: `${n.pct}%` }} />
                              </div>
                              <span className="text-xs font-bold text-white font-mono w-10 text-right">{n.pct}%</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Secondary stats */}
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                <StatCard label="Feeder Line Drops" value={results.averages.feeder_drops.toFixed(1)} />
                <StatCard label="Pumps on Backup Gen" value={results.averages.pumps_backup.toFixed(1)} />
                <StatCard label="Telecom on Battery" value={results.averages.telecom_battery.toFixed(1)} />
                <StatCard label="Towers Draining" value={results.averages.towers_draining.toFixed(1)} />
                <StatCard label="Avg Water Pressure" value={results.averages.avg_pressure.toFixed(3)} />
                <StatCard label="Substations Failed" value={results.averages.subs_failed.toFixed(2)} />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
"use client";

import { useEffect, useState } from "react";
import { Clock, Play, Pause, SkipBack, SkipForward, FileJson } from "lucide-react";

export default function ReplayPage() {
  const [history, setHistory] = useState<any[]>([]);
  const [pos, setPos] = useState(0);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    fetch("http://localhost:8000/api/events/history")
      .then(res => res.json())
      .then(data => setHistory(data.events || []))
      .catch(console.error);
  }, []);

  useEffect(() => {
    if (!playing || history.length === 0) return;
    
    const interval = setInterval(() => {
      setPos(p => {
        if (p >= history.length - 1) {
          setPlaying(false);
          return p;
        }
        return p + 1;
      });
    }, 500); // 500ms per event step

    return () => clearInterval(interval);
  }, [playing, history.length]);

  if (history.length === 0) {
    return (
      <div className="p-8 max-w-4xl mx-auto w-full h-full flex items-center justify-center">
        <div className="text-center glass-panel p-10 max-w-md">
          <FileJson className="w-12 h-12 text-gray-500 mx-auto mb-4" />
          <h2 className="text-xl font-bold mb-2">No Event History Found</h2>
          <p className="text-gray-400 text-sm">
            Run a scenario in the Simulation tab first to generate an events.jsonl log.
          </p>
        </div>
      </div>
    );
  }

  const currentEvent = history[pos];

  return (
    <div className="p-8 max-w-4xl mx-auto w-full h-full flex flex-col">
      <div className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight mb-2">Replay Viewer</h1>
        <p className="text-gray-400">Step through recorded simulation runs event by event.</p>
      </div>

      <div className="glass-panel p-6 flex-1 flex flex-col">
        {/* Scrubber Controls */}
        <div className="flex items-center gap-4 mb-8 bg-gray-800/50 p-4 rounded-xl border border-gray-700">
          <button onClick={() => setPos(0)} className="p-2 hover:bg-gray-700 rounded"><SkipBack className="w-5 h-5"/></button>
          <button onClick={() => setPlaying(!playing)} className="p-3 bg-blue-600 hover:bg-blue-500 rounded-full">
            {playing ? <Pause className="w-6 h-6"/> : <Play className="w-6 h-6"/>}
          </button>
          <button onClick={() => setPos(Math.min(history.length - 1, pos + 1))} className="p-2 hover:bg-gray-700 rounded"><SkipForward className="w-5 h-5"/></button>
          
          <div className="flex-1 mx-4">
            <input 
              type="range" 
              min="0" 
              max={history.length - 1} 
              value={pos} 
              onChange={(e) => {
                setPos(parseInt(e.target.value));
                setPlaying(false);
              }}
              className="w-full cursor-pointer"
            />
            <div className="flex justify-between text-xs text-gray-400 mt-1">
              <span>Event 1</span>
              <span>Event {pos + 1} of {history.length}</span>
            </div>
          </div>
        </div>

        {/* Current Event Details */}
        <div className="flex-1 border border-gray-700 rounded-xl bg-black/30 p-6 flex flex-col">
          <div className="flex items-center justify-between mb-6 pb-4 border-b border-gray-800">
            <div className="flex items-center gap-3">
              <Clock className="text-gray-500" />
              <span className="text-xl font-mono">Tick {currentEvent.tick}</span>
            </div>
            <span className={`px-3 py-1 rounded border text-sm font-bold uppercase bg-${currentEvent.source_network}/10 text-${currentEvent.source_network} border-${currentEvent.source_network}`}>
              {currentEvent.source_network}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-8">
            <div>
              <h3 className="text-sm text-gray-500 mb-1">Event Type</h3>
              <div className="text-2xl font-bold text-white mb-6">{currentEvent.event_type}</div>
              
              <h3 className="text-sm text-gray-500 mb-1">Source Asset</h3>
              <div className="text-xl font-bold text-blue-300 mb-1">{currentEvent.node_name || "Infrastructure Asset"}</div>
              <div className="font-mono text-sm text-gray-400 mb-6">Ref ID: {currentEvent.node_id}</div>
              
              <h3 className="text-sm text-gray-500 mb-1">Severity / Impact</h3>
              <div className="flex items-end gap-2 mb-6">
                <span className="text-3xl font-bold text-danger">{(currentEvent.severity * 100).toFixed(0)}%</span>
                <span className="text-sm text-gray-400 mb-1">Depth: {currentEvent.cascade_depth || 0}</span>
              </div>
            </div>
            
            <div className="bg-gray-900/50 rounded-lg p-4 font-mono text-xs overflow-auto border border-gray-800">
              <div className="text-gray-500 mb-2">// Raw JSON</div>
              <pre className="text-green-400">
                {JSON.stringify(currentEvent, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

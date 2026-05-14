"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { 
  Activity, 
  Map, 
  Zap, 
  Droplet, 
  Wifi, 
  Shuffle, 
  PlayCircle, 
  Clock, 
  Layers,
  Database,
  ShieldCheck
} from "lucide-react";

export default function Sidebar() {
  const pathname = usePathname();

  const links = [
    { href: "/", label: "Operations Center", icon: Activity, color: "text-blue-400" },
    { href: "/map/combined", label: "Cascade Overlay", icon: Map, color: "text-white" },
    { href: "/map/power", label: "Power Grid", icon: Zap, color: "text-power" },
    { href: "/map/water", label: "Water System", icon: Droplet, color: "text-water" },
    { href: "/map/telecom", label: "Telecom Setup", icon: Wifi, color: "text-telecom" },
    { href: "/simulation", label: "Strategic Engine", icon: PlayCircle, color: "text-purple-400" },
    { href: "/replay", label: "Replay Viewer", icon: Clock, color: "text-slate-400" },
  ];

  return (
    <div className="w-72 glass border-r border-card-border h-full flex flex-col z-50 relative overflow-hidden">
      {/* Decorative background glow */}
      <div className="absolute top-0 left-0 w-full h-64 bg-gradient-to-b from-blue-500/5 to-transparent pointer-events-none" />
      
      <div className="p-8 border-b border-white/5 relative">
        <div className="flex items-center gap-3 mb-2">
          <div className="p-2 bg-white/5 rounded-xl border border-white/10 shadow-inner">
            <Layers className="w-6 h-6 text-blue-500" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white font-display">
            URBAN<span className="text-blue-500">TWIN</span>
          </h1>
        </div>
        <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500 font-bold">
          Halasuru Strategic Node
        </p>
      </div>
      
      <nav className="flex-1 py-8 overflow-y-auto custom-scrollbar">
        <div className="px-4 mb-6">
          <p className="text-[10px] uppercase tracking-widest text-slate-600 font-black px-4 mb-4">
            Intelligence Units
          </p>
          <ul className="space-y-1.5">
            {links.map((link) => {
              const isActive = pathname === link.href;
              const Icon = link.icon;
              return (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    className={`group flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-300 relative ${
                      isActive 
                        ? "bg-white/5 text-white shadow-[inset_0_1px_1px_rgba(255,255,255,0.05)]" 
                        : "text-slate-500 hover:text-slate-300 hover:bg-white/[0.02]"
                    }`}
                  >
                    {isActive && (
                      <div className="absolute left-0 top-1/4 bottom-1/4 w-1 bg-blue-500 rounded-full shadow-[0_0_8px_#3b82f6]" />
                    )}
                    <Icon className={`w-5 h-5 transition-transform duration-300 group-hover:scale-110 ${isActive ? link.color : 'text-slate-600'}`} />
                    <span className="text-sm font-medium tracking-wide">{link.label}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="px-4 mt-8">
          <p className="text-[10px] uppercase tracking-widest text-slate-600 font-black px-4 mb-4">
            System Data
          </p>
          <ul className="space-y-1.5">
            <li>
              <div className="flex items-center gap-3 px-4 py-3 text-slate-600 cursor-not-allowed">
                <Database className="w-5 h-5" />
                <span className="text-sm font-medium">OSM Vector Set</span>
              </div>
            </li>
          </ul>
        </div>
      </nav>

      <div className="p-6 border-t border-white/5 bg-black/20">
        <div className="glass-panel p-3 rounded-xl border border-green-500/20 bg-green-500/5">
          <div className="flex items-center gap-3 text-[11px] font-bold text-green-500 uppercase tracking-tighter">
            <ShieldCheck className="w-4 h-4" />
            System Secure
          </div>
          <div className="text-[9px] text-green-500/50 mt-1 ml-7">
            Node uptime: 14d 02h 12m
          </div>
        </div>
      </div>
    </div>
  );
}


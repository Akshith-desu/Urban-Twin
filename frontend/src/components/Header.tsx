import React from 'react';
import { useStore } from '../store/useStore';
import { Zap, Droplets, Radio, Network as NetworkIcon, Play, Square, RefreshCcw, Activity } from 'lucide-react';
import type { Page } from '../store/useStore';

const Header: React.FC = () => {
  const { activePage, setActivePage, currentTick, phase, setPhase, resetSimulation } = useStore();

  const navItems: { id: Page; label: string; icon: React.ReactNode }[] = [
    { id: 'cascade', label: 'Cascade Overlay', icon: <NetworkIcon size={18} /> },
    { id: 'power', label: 'Power Grid', icon: <Zap size={18} /> },
    { id: 'water', label: 'Water Network', icon: <Droplets size={18} /> },
    { id: 'telecom', label: 'Telecom Towers', icon: <Radio size={18} /> },
    { id: 'monte-carlo', label: 'Monte Carlo', icon: <Activity size={18} /> },
  ];

  return (
    <header className="top-bar">
      <div style={{ display: 'flex', alignItems: 'center', gap: 32 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ 
            width: 32, 
            height: 32, 
            backgroundColor: '#3b82f6', 
            borderRadius: '8px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontWeight: 800,
            fontSize: 20
          }}>U</div>
          <h1 style={{ fontSize: '18px', fontWeight: 700, letterSpacing: '-0.02em' }}>UrbanTwin <span style={{ color: '#94a3b8', fontWeight: 400 }}>v2.0</span></h1>
        </div>

        <nav className="nav-links">
          {navItems.map((item) => (
            <button
              key={item.id}
              className={`nav-link ${activePage === item.id ? 'active' : ''}`}
              onClick={() => setActivePage(item.id)}
              style={{ display: 'flex', alignItems: 'center', gap: 8 }}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </nav>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: '10px', color: '#94a3b8', textTransform: 'uppercase' }}>Simulation Tick</div>
          <div style={{ fontSize: '16px', fontWeight: 700, fontFamily: 'monospace' }}>
            T + {String(currentTick).padStart(4, '0')}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          {phase === 'running' ? (
            <button 
              onClick={() => setPhase('idle')}
              style={{
                backgroundColor: 'rgba(239, 68, 68, 0.1)',
                color: '#ef4444',
                border: '1px solid rgba(239, 68, 68, 0.2)',
                padding: '8px 16px',
                borderRadius: '8px',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                cursor: 'pointer'
              }}
            >
              <Square size={16} fill="currentColor" /> Pause
            </button>
          ) : (
            <button 
              onClick={() => setPhase('running')}
              style={{
                backgroundColor: 'rgba(16, 185, 129, 0.1)',
                color: '#10b981',
                border: '1px solid rgba(16, 185, 129, 0.2)',
                padding: '8px 16px',
                borderRadius: '8px',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                cursor: 'pointer'
              }}
            >
              <Play size={16} fill="currentColor" /> Resume
            </button>
          )}
          
          <button 
            onClick={resetSimulation}
            style={{
              backgroundColor: 'var(--bg-tertiary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-color)',
              padding: '8px',
              borderRadius: '8px',
              cursor: 'pointer'
            }}
          >
            <RefreshCcw size={18} />
          </button>
        </div>
      </div>
    </header>
  );
};

export default Header;

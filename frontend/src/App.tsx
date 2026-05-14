import React, { useEffect } from 'react';
import { useStore } from './store/useStore';
import { loadAllGraphs, buildNodeCoordMap } from './data/loader';
import MapView from './components/Map/MapView';
import Sidebar from './components/Sidebar';
import Header from './components/Header';
import { Activity, Zap, Droplets, Radio } from 'lucide-react';
import { useSimulation } from './simulation/simController';
import MonteCarloView from './pages/MonteCarloView';

const App: React.FC = () => {
  const { setGraphs, activePage } = useStore();
  
  // Start simulation logic
  useSimulation();

  useEffect(() => {
    async function init() {
      const { power, water, telecom } = await loadAllGraphs();
      const coordMap = buildNodeCoordMap(power, water, telecom);
      setGraphs(power, water, telecom, coordMap);
    }
    init();
  }, [setGraphs]);

  return (
    <div className="app-container">
      <Sidebar />
      <main className="main-content">
        <Header />
        <div style={{ flex: 1, position: 'relative' }}>
          {activePage === 'cascade' || activePage === 'power' || activePage === 'water' || activePage === 'telecom' ? (
            <MapView />
          ) : activePage === 'monte-carlo' ? (
            <div style={{ overflowY: 'auto', height: '100%', backgroundColor: 'var(--bg-primary)' }}>
              <MonteCarloView />
            </div>
          ) : null}
        </div>
        
        {/* Quick Stats Overlay */}
        <div style={{
          position: 'absolute',
          bottom: 24,
          right: 24,
          display: 'flex',
          gap: 12,
          zIndex: 5
        }}>
          <StatMini label="Power" value="98%" icon={<Zap size={16} color="#3b82f6" />} />
          <StatMini label="Water" value="94%" icon={<Droplets size={16} color="#10b981" />} />
          <StatMini label="Telecom" value="100%" icon={<Radio size={16} color="#f59e0b" />} />
        </div>
      </main>
    </div>
  );
};

const StatMini = ({ label, value, icon }: { label: string, value: string, icon: React.ReactNode }) => (
  <div style={{
    background: 'rgba(20, 20, 24, 0.9)',
    backdropFilter: 'blur(8px)',
    border: '1px solid #2e2e38',
    borderRadius: '8px',
    padding: '8px 12px',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    boxShadow: '0 4px 12px rgba(0,0,0,0.5)'
  }}>
    {icon}
    <div>
      <div style={{ fontSize: '10px', color: '#94a3b8', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: '14px', fontWeight: 700 }}>{value}</div>
    </div>
  </div>
);

export default App;

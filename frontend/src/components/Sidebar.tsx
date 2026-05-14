import React from 'react';
import { useStore } from '../store/useStore';
import { AlertCircle, Activity, Info, ChevronRight, X } from 'lucide-react';

import { useSimulation } from '../simulation/simController';

const Sidebar: React.FC = () => {
  const { isSidebarOpen, events, selectedNode, setSelectedNode, failedNodes } = useStore();
  const { triggerFailure, triggerRestore } = useSimulation();

  if (!isSidebarOpen) return null;

  return (
    <aside className="sidebar">
      <div style={{ padding: '24px', borderBottom: '1px solid var(--border-color)' }}>
        <h2 style={{ fontSize: '14px', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Event Log</h2>
      </div>
      
      <div className="event-log">
        {events.length === 0 ? (
          <div style={{ padding: '24px', textAlign: 'center', color: '#64748b' }}>
            <Activity size={32} style={{ marginBottom: 12, opacity: 0.3 }} />
            <p style={{ fontSize: '14px' }}>Monitoring bus for events...</p>
          </div>
        ) : (
          [...events].reverse().map((event, idx) => (
            <div key={event.event_id || idx} className="event-item" style={{
              borderLeftColor: getEventColor(event.event_type)
            }}>
              <div className="event-header">
                <span className="event-type" style={{ color: getEventColor(event.event_type) }}>
                  {event.event_type.replace(/_/g, ' ')}
                </span>
                <span className="event-tick">T+{event.tick}</span>
              </div>
              <p className="event-msg">
                Node: <span style={{ color: '#f8fafc' }}>{event.node_id}</span> | Depth: {event.cascade_depth}
              </p>
            </div>
          ))
        )}
      </div>

      {selectedNode && (
        <div style={{ 
          position: 'absolute', 
          bottom: 0, 
          left: 0, 
          right: 0, 
          backgroundColor: 'var(--bg-tertiary)',
          borderTop: '1px solid var(--border-color)',
          padding: '20px',
          boxShadow: '0 -10px 20px rgba(0,0,0,0.3)',
          zIndex: 20
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Info size={16} color="#3b82f6" />
              <h3 style={{ fontSize: '16px', fontWeight: 600 }}>Node Inspector</h3>
            </div>
            <button 
              onClick={() => setSelectedNode(null)}
              style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}
            >
              <X size={20} />
            </button>
          </div>

          <div className="card" style={{ marginBottom: 0, background: 'var(--bg-secondary)' }}>
            <div style={{ fontSize: '11px', color: '#94a3b8', textTransform: 'uppercase', marginBottom: 4 }}>{selectedNode.network}</div>
            <div style={{ fontSize: '18px', fontWeight: 700, marginBottom: 12 }}>{selectedNode.name}</div>
            
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
              <div className="stat-group">
                <div className="stat-label">Health</div>
                <div className="stat-value" style={{ color: '#10b981' }}>100%</div>
              </div>
              <div className="stat-group">
                <div className="stat-label">Status</div>
                <div className="stat-value" style={{ color: '#3b82f6', fontSize: '16px', marginTop: '8px' }}>Operational</div>
              </div>
            </div>

            {failedNodes.has(selectedNode.id) ? (
              <button
                onClick={() => triggerRestore(selectedNode.id, selectedNode.network)}
                style={{
                  width: '100%',
                  backgroundColor: 'rgba(16, 185, 129, 0.1)',
                  color: '#10b981',
                  border: '1px solid rgba(16, 185, 129, 0.2)',
                  padding: '10px',
                  borderRadius: '8px',
                  fontWeight: 600,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 8
                }}
              >
                <Activity size={18} /> RESTORE NODE
              </button>
            ) : (
              <button
                onClick={() => triggerFailure(selectedNode.id, selectedNode.network)}
                style={{
                  width: '100%',
                  backgroundColor: 'rgba(239, 68, 68, 0.1)',
                  color: '#ef4444',
                  border: '1px solid rgba(239, 68, 68, 0.2)',
                  padding: '10px',
                  borderRadius: '8px',
                  fontWeight: 600,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 8
                }}
              >
                <AlertCircle size={18} /> FAIL NODE
              </button>
            )}
          </div>
        </div>
      )}
    </aside>
  );
};

const getEventColor = (type: string) => {
  if (type.includes('FAIL') || type.includes('BURST') || type.includes('LOSS')) return '#ef4444';
  if (type.includes('DEGRADE') || type.includes('DROP') || type.includes('OVERLOAD')) return '#f59e0b';
  if (type.includes('RECOVER')) return '#10b981';
  return '#3b82f6';
};

export default Sidebar;

import React from 'react';
import { useStore } from '../store/useStore';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LineChart, Line } from 'recharts';

const MonteCarloView: React.FC = () => {
  const { events } = useStore();

  // Mock data for Monte Carlo results (until real data is plugged in)
  const data = [
    { name: 'Run 1', failures: 12, depth: 3 },
    { name: 'Run 2', failures: 8, depth: 2 },
    { name: 'Run 3', failures: 25, depth: 5 },
    { name: 'Run 4', failures: 15, depth: 4 },
    { name: 'Run 5', failures: 10, depth: 3 },
    { name: 'Run 6', failures: 18, depth: 4 },
  ];

  return (
    <div style={{ padding: '24px', color: 'var(--text-primary)' }}>
      <h2 style={{ fontSize: '24px', fontWeight: 700, marginBottom: '24px' }}>Monte Carlo Simulation Results</h2>
      
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px', marginBottom: '24px' }}>
        <div className="card">
          <h3 style={{ fontSize: '14px', color: '#94a3b8', marginBottom: '16px' }}>Failure Count Distribution</h3>
          <div style={{ height: '300px' }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2e2e38" />
                <XAxis dataKey="name" stroke="#94a3b8" />
                <YAxis stroke="#94a3b8" />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#1c1c22', border: '1px solid #2e2e38' }}
                  itemStyle={{ color: '#f8fafc' }}
                />
                <Bar dataKey="failures" fill="#3b82f6" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="card">
          <h3 style={{ fontSize: '14px', color: '#94a3b8', marginBottom: '16px' }}>Max Cascade Depth</h3>
          <div style={{ height: '300px' }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2e2e38" />
                <XAxis dataKey="name" stroke="#94a3b8" />
                <YAxis stroke="#94a3b8" />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#1c1c22', border: '1px solid #2e2e38' }}
                  itemStyle={{ color: '#f8fafc' }}
                />
                <Line type="monotone" dataKey="depth" stroke="#10b981" strokeWidth={3} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="card">
        <h3 style={{ fontSize: '14px', color: '#94a3b8', marginBottom: '16px' }}>Critical Insights</h3>
        <ul style={{ listArr: 'none', color: '#94a3b8', fontSize: '14px' }}>
          <li style={{ marginBottom: '12px' }}>
            <strong style={{ color: '#ef4444' }}>High Vulnerability:</strong> Substation 1 shows 85% failure probability in flood scenarios.
          </li>
          <li style={{ marginBottom: '12px' }}>
            <strong style={{ color: '#f59e0b' }}>Cascading Risk:</strong> Power failure in Indiranagar region has a 92% chance of disabling the primary water pump.
          </li>
          <li>
            <strong style={{ color: '#10b981' }}>Recovery Metric:</strong> Average recovery time for telecom towers is 12 ticks (60 mins).
          </li>
        </ul>
      </div>
    </div>
  );
};

export default MonteCarloView;

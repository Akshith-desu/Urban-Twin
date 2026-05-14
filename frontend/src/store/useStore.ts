import { create } from 'zustand';

export enum Network {
  POWER = 'power',
  WATER = 'water',
  ROAD = 'road',
  TELECOM = 'telecom',
  SYSTEM = 'system'
}

export enum OperationalStatus {
  NORMAL = 'normal',
  DEGRADED = 'degraded',
  FAILED = 'failed',
  ON_BACKUP = 'on_backup'
}

export enum Page {
  CASCADE = 'cascade',
  POWER = 'power',
  TELECOM = 'telecom',
  WATER = 'water',
  MONTE_CARLO = 'monte-carlo'
}

export enum SimulationPhase {
  IDLE = 'idle',
  RUNNING = 'running',
  MONTE_CARLO = 'monte-carlo',
  COMPLETE = 'complete'
}

export interface SelectedNode {
  id: string;
  network: string;
  name: string;
}

export interface CascadeLink {
  sourcePosition: [number, number];
  targetPosition: [number, number];
  sourceNode: string;
  targetNode: string;
  sourceNetwork: string;
  targetNetwork: string;
  tick: number;
  depth: number;
}

export interface SimEvent {
  event_id: string;
  timestamp: number;
  tick: number;
  event_type: string;
  source_network: string;
  node_id: string;
  severity: number;
  affected_nodes: string[];
  cascade_depth: number;
  metadata: any;
}

export interface PowerNode {
  node_id: string;
  name: string;
  power: string;
  latitude: number;
  longitude: number;
  x: number;
  y: number;
  rated_capacity_mw: number;
  voltage_kv: number;
  impedance_ohm: number;
  health: number;
  operational_status: string;
  load_fraction: number;
  on_grid_power: boolean;
  flood_risk: boolean;
}

export interface PowerGraph {
  nodes: PowerNode[];
  edges: any[];
}

export interface WaterNode {
  node_id: string;
  name: string;
  node_type: string;
  x: number;
  y: number;
  lat?: number;
  lon?: number;
  health: number;
  operational_status: string;
}

export interface WaterGraph {
  nodes: WaterNode[];
  edges: any[];
}

export interface TelecomNode {
  node_id: string | number;
  name: string;
  tower_type: string;
  latitude: number;
  longitude: number;
  health: number;
  operational_status: string;
  battery: any;
  providers: any[];
}

export interface TelecomGraph {
  nodes: TelecomNode[];
}

interface GlobalState {
  powerGraph: PowerGraph | null;
  waterGraph: WaterGraph | null;
  telecomGraph: TelecomGraph | null;
  coordMap: Map<string, [number, number]>;
  currentTick: number;
  phase: string;
  events: SimEvent[];
  cascadeLinks: CascadeLink[];
  failedNodes: Set<string>;
  activePage: string;
  selectedNode: SelectedNode | null;
  isSidebarOpen: boolean;
  setGraphs: (power: PowerGraph, water: WaterGraph, telecom: TelecomGraph, coordMap: Map<string, [number, number]>) => void;
  setActivePage: (page: string) => void;
  setSelectedNode: (node: SelectedNode | null) => void;
  toggleSidebar: () => void;
  addEvent: (event: SimEvent) => void;
  setTick: (tick: number) => void;
  setPhase: (phase: string) => void;
  resetSimulation: () => void;
  addCascadeLink: (link: CascadeLink) => void;
  failNode: (nodeId: string, network: string) => void;
  restoreNode: (nodeId: string) => void;
}

export const useStore = create<GlobalState>((set, get) => ({
  powerGraph: null,
  waterGraph: null,
  telecomGraph: null,
  coordMap: new Map(),
  currentTick: 0,
  phase: 'idle',
  events: [],
  cascadeLinks: [],
  failedNodes: new Set(),
  activePage: 'cascade',
  selectedNode: null,
  isSidebarOpen: true,
  setGraphs: (power, water, telecom, coordMap) => set({ powerGraph: power, waterGraph: water, telecomGraph: telecom, coordMap }),
  setActivePage: (page) => set({ activePage: page }),
  setSelectedNode: (node) => set({ selectedNode: node }),
  toggleSidebar: () => set((state) => ({ isSidebarOpen: !state.isSidebarOpen })),
  addEvent: (event) => set((state) => {
    // Prevent duplicate events (can happen with WebSocket reconnects or HMR)
    if (state.events.some(e => e.event_id === event.event_id)) {
      return state;
    }
    return { events: [...state.events, event] };
  }),
  setTick: (tick) => set({ currentTick: tick }),
  setPhase: (phase) => set({ phase }),
  resetSimulation: () => {
    fetch('http://127.0.0.1:8000/reset', { method: 'POST' }).catch(console.error);
    set({ currentTick: 0, events: [], cascadeLinks: [], failedNodes: new Set(), phase: 'idle' });
  },
  addCascadeLink: (link) => set((state) => {
    // Prevent duplicate visual links for the same event/tick
    const isDuplicate = state.cascadeLinks.some(l => 
        l.sourceNode === link.sourceNode && 
        l.targetNode === link.targetNode && 
        l.tick === link.tick
    );
    if (isDuplicate) return state;
    return { cascadeLinks: [...state.cascadeLinks, link] };
  }),
  failNode: (nodeId, network) => {
    const { failedNodes } = get();
    if (failedNodes.has(nodeId)) return;
    const newFailed = new Set(failedNodes);
    newFailed.add(nodeId);
    set({ failedNodes: newFailed });
  },
  restoreNode: (nodeId) => {
    const { failedNodes, cascadeLinks } = get();
    if (!failedNodes.has(nodeId)) return;
    const newFailed = new Set(failedNodes);
    newFailed.delete(nodeId);
    // Remove cascade links originating from or pointing to this node
    const newLinks = cascadeLinks.filter(l => l.sourceNode !== nodeId && l.targetNode !== nodeId);
    set({ failedNodes: newFailed, cascadeLinks: newLinks });
  },
}));

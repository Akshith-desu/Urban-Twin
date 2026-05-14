import React, { useEffect, useRef, useMemo, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useStore } from '../../store/useStore';
import type { Network } from '../../store/useStore';

const MapView: React.FC = () => {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const { 
    powerGraph, 
    waterGraph, 
    telecomGraph, 
    activePage, 
    setSelectedNode,
    failedNodes,
    cascadeLinks,
    coordMap
  } = useStore();

  // Pre-index power nodes for fast edge lookups
  const powerNodeMap = useMemo(() => {
    const m = new Map<string, any>();
    powerGraph?.nodes.forEach(n => {
      m.set(n.name, n);
      m.set(n.node_id, n);
    });
    return m;
  }, [powerGraph]);

  // Pre-index water nodes for fast edge lookups
  const waterNodeMap = useMemo(() => {
    const m = new Map<string, any>();
    waterGraph?.nodes.forEach(n => {
      m.set(n.name, n);
      m.set(n.node_id, n);
    });
    return m;
  }, [waterGraph]);

  useEffect(() => {
    if (map.current || !mapContainer.current) return;

    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
      center: [77.62, 12.97], // Bengaluru
      zoom: 13,
      antialias: true
    });

    map.current.on('load', () => {
      if (!map.current) return;
      if (map.current.getSource('infrastructure')) return;

      map.current.addSource('infrastructure', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
      });

      map.current.addLayer({
        id: 'power-edges-layer',
        type: 'line',
        source: 'infrastructure',
        filter: ['==', ['get', 'item_type'], 'edge'],
        paint: {
          'line-color': [
            'match', ['get', 'network'],
            'power', '#3b82f6',
            'water', '#10b981',
            'telecom', '#f59e0b',
            '#fff'
          ],
          'line-width': 1.5,
          'line-opacity': 0.6
        }
      });

      map.current.addLayer({
        id: 'dependency-edges-layer',
        type: 'line',
        source: 'infrastructure',
        filter: ['all', ['==', ['get', 'item_type'], 'link'], ['==', ['get', 'network'], 'dependency']],
        paint: {
          'line-color': 'rgba(255, 255, 255, 0.4)',
          'line-width': 1,
          'line-dasharray': [2, 2],
          'line-opacity': 0.8
        }
      });

      map.current.addLayer({
        id: 'cascade-edges-layer',
        type: 'line',
        source: 'infrastructure',
        filter: ['all', ['==', ['get', 'item_type'], 'link'], ['==', ['get', 'network'], 'cascade']],
        paint: {
          'line-color': '#ff0000', // Pure red
          'line-width': 5,         // Much thicker
          'line-opacity': 1.0
        },
        layout: {
          'line-cap': 'round',
          'line-join': 'round'
        }
      });

      map.current.addLayer({
        id: 'nodes-layer',
        type: 'circle',
        source: 'infrastructure',
        filter: ['all', ['!=', ['get', 'item_type'], 'edge'], ['!=', ['get', 'item_type'], 'link']],
        paint: {
          'circle-radius': [
            'match', ['get', 'item_type'],
            'substation', 10,
            'pump_station', 8,
            'transformer', 5,
            'water_tower', 7,
            'ground', 6,
            5
          ],
          'circle-color': [
            'case',
            ['==', ['get', 'status'], 'failed'], '#ef4444',
            ['match', ['get', 'network'], 'power', '#3b82f6', 'water', '#10b981', 'telecom', '#f59e0b', '#fff']
          ],
          'circle-stroke-width': 2,
          'circle-stroke-color': '#000'
        }
      });

      map.current.on('click', 'nodes-layer', (e) => {
        if (!e.features || e.features.length === 0) return;
        const feat = e.features[0];
        const props = feat.properties;
        setSelectedNode({
          id: props.id,
          name: props.name,
          network: props.network as Network
        });
      });

      map.current.on('mouseenter', 'nodes-layer', () => {
        if (map.current) map.current.getCanvas().style.cursor = 'pointer';
      });

      map.current.on('mouseleave', 'nodes-layer', () => {
        if (map.current) map.current.getCanvas().style.cursor = '';
      });
    });

    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, [setSelectedNode]);

  // Keep track of feature counts for debug overlay
  const [debugCounts, setDebugCounts] = useState({ nodes: 0, powerEdges: 0, depEdges: 0, cascadeEdges: 0 });

  useEffect(() => {
    if (!map.current) return;
    
    const updateData = () => {
      if (!map.current) return;
      const source = map.current.getSource('infrastructure') as maplibregl.GeoJSONSource;
      if (!source) return;

      const features: any[] = [];
      const addNodes = (nodes: any[] | undefined, network: string, typeField: string) => {
        nodes?.forEach(n => {
          const id = String(n.node_id);
          const lon = n.longitude || n.lon;
          const lat = n.latitude || n.lat;
          if (lon && lat) {
            features.push({
              type: 'Feature',
              geometry: { type: 'Point', coordinates: [lon, lat] },
              properties: { 
                id, 
                name: n.name, 
                network, 
                item_type: n[typeField],
                status: failedNodes.has(id) ? 'failed' : 'operational'
              }
            });
          }
        });
      };

      if (activePage === 'cascade' || activePage === 'power') addNodes(powerGraph?.nodes, 'power', 'power');
      if (activePage === 'cascade' || activePage === 'water') addNodes(waterGraph?.nodes, 'water', 'node_type');
      if (activePage === 'cascade' || activePage === 'telecom') addNodes(telecomGraph?.nodes, 'telecom', 'tower_type');

      // Add Power Edges (Optimized)
      if (activePage === 'cascade' || activePage === 'power') {
          powerGraph?.edges.forEach(edge => {
              const fromNode = powerNodeMap.get(edge.from);
              const toNode = powerNodeMap.get(edge.to);
              if (fromNode && toNode) {
                  features.push({
                      type: 'Feature',
                      geometry: { type: 'LineString', coordinates: [[fromNode.longitude, fromNode.latitude], [toNode.longitude, toNode.latitude]] },
                      properties: { network: 'power', item_type: 'edge' }
                  });
              }
          });
      }

      // Add Water Edges
      if (activePage === 'cascade' || activePage === 'water') {
          waterGraph?.edges.forEach(edge => {
              const fromNode = waterNodeMap.get(edge.from);
              const toNode = waterNodeMap.get(edge.to);
              if (fromNode && toNode) {
                  features.push({
                      type: 'Feature',
                      geometry: { type: 'LineString', coordinates: [[fromNode.lon || fromNode.longitude, fromNode.lat || fromNode.latitude], [toNode.lon || toNode.longitude, toNode.lat || toNode.latitude]] },
                      properties: { network: 'water', item_type: 'edge' }
                  });
              }
          });
      }

      // Add Active Cascade Links
      if (cascadeLinks.length > 0) {
          console.log(`%c[Map] Rendering ${cascadeLinks.length} cascade links`, 'color: #ff0000; font-weight: bold');
      }
      cascadeLinks.forEach(link => {
        features.push({
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: [link.sourcePosition, link.targetPosition] },
          properties: { network: 'cascade', item_type: 'link' }
        });
      });

      // Add Static Dependency Links (Interlinking)
      if (activePage === 'cascade' || activePage === 'water') {
          waterGraph?.nodes.forEach(wn => {
              if (wn.power_dependency) {
                  const sourcePos = coordMap.get(`power:${wn.power_dependency}`);
                  const targetPos = coordMap.get(`water:${wn.node_id}`);
                  if (sourcePos && targetPos) {
                      features.push({
                          type: 'Feature',
                          geometry: { type: 'LineString', coordinates: [[sourcePos[1], sourcePos[0]], [targetPos[1], targetPos[0]]] },
                          properties: { network: 'dependency', item_type: 'link' }
                      });
                  }
              }
          });
      }

      if (activePage === 'cascade' || activePage === 'telecom') {
          telecomGraph?.nodes.forEach(tn => {
              if (tn.power_dependency) {
                  const sourcePos = coordMap.get(`power:${tn.power_dependency}`);
                  const targetPos = coordMap.get(`telecom:${tn.node_id}`);
                  if (sourcePos && targetPos) {
                      features.push({
                          type: 'Feature',
                          geometry: { type: 'LineString', coordinates: [[sourcePos[1], sourcePos[0]], [targetPos[1], targetPos[0]]] },
                          properties: { network: 'dependency', item_type: 'link' }
                      });
                  }
              }
          });
      }

      source.setData({ type: 'FeatureCollection', features });

      // Update debug counts
      setDebugCounts({
        nodes: features.filter(f => f.geometry.type === 'Point').length,
        powerEdges: features.filter(f => f.properties.network === 'power' && f.geometry.type === 'LineString').length,
        depEdges: features.filter(f => f.properties.network === 'dependency' && f.geometry.type === 'LineString').length,
        cascadeEdges: features.filter(f => f.properties.network === 'cascade' && f.geometry.type === 'LineString').length
      });
    };

    if (map.current.isStyleLoaded()) {
      updateData();
    } else {
      map.current.once('styledata', updateData);
    }
  }, [powerGraph, waterGraph, telecomGraph, activePage, failedNodes, cascadeLinks, coordMap, powerNodeMap]);

  return (
    <>
      <div ref={mapContainer} className="map-container" style={{ width: '100%', height: '100%' }} />
      <div style={{ position: 'absolute', top: 10, left: 10, background: 'rgba(0,0,0,0.8)', padding: 10, color: '#fff', zIndex: 1000, borderRadius: 4, fontFamily: 'monospace' }}>
        <div>DEBUG INFO</div>
        <div>Nodes: {debugCounts.nodes}</div>
        <div>Power Edges: {debugCounts.powerEdges}</div>
        <div>Dep Edges: {debugCounts.depEdges}</div>
        <div>Cascade Edges: {debugCounts.cascadeEdges}</div>
        <div>Active Page: {activePage}</div>
      </div>
    </>
  );
};

export default MapView;

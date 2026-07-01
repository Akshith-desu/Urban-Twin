const API_BASE_URL = "http://localhost:8000/api";

export async function fetchNetwork(network: string) {
  try {
    const res = await fetch(`${API_BASE_URL}/graphs/${network}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch network data');
    const data = await res.json();
    // Return the nodes + edges as a combined FeatureCollection for the map
    return {
      type: "FeatureCollection",
      features: [...(data.nodes?.features || []), ...(data.edges?.features || [])],
      stats: data.stats
    };
  } catch (error) {
    console.error(`Error fetching ${network}:`, error);
    return null;
  }
}

export async function fetchCombined() {
  try {
    const res = await fetch(`${API_BASE_URL}/graphs/combined/all`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch combined network data');
    return await res.json();
  } catch (error) {
    console.error(`Error fetching combined:`, error);
    return null;
  }
}

export async function fetchStats() {
  try {
    const res = await fetch(`${API_BASE_URL}/stats`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch stats');
    return await res.json();
  } catch (error) {
    console.error(`Error fetching stats:`, error);
    return null;
  }
}
export async function fetchEvents() {
  try {
    const res = await fetch(`${API_BASE_URL}/events/history`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch events');
    return await res.json();
  } catch (error) {
    console.error(`Error fetching events:`, error);
    return { events: [], total: 0 };
  }
}

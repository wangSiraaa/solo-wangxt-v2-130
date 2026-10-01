import { useEffect, useRef } from 'react';
import cytoscape from 'cytoscape';

export default function NetworkGraph({ topology }) {
  const ref = useRef(null);
  const cyRef = useRef(null);

  useEffect(() => {
    if (!ref.current || !topology) return undefined;
    const cy = cytoscape({
      container: ref.current,
      elements: [...topology.nodes, ...topology.edges],
      style: [
        {
          selector: 'node',
          style: {
            label: 'data(label)',
            'background-color': '#2563eb',
            color: '#111827',
            'font-size': 10,
            'text-valign': 'bottom',
            width: 18,
            height: 18,
          },
        },
        {
          selector: 'node[data(is_datum)]',
          style: { 'background-color': '#dc2626', 'border-width': 3, 'border-color': '#f59e0b' },
        },
        {
          selector: 'edge',
          style: {
            label: 'data(label)',
            width: 1.4,
            'line-color': '#64748b',
            'target-arrow-color': '#64748b',
            'target-arrow-shape': 'triangle',
            'font-size': 8,
            'curve-style': 'bezier',
          },
        },
      ],
      layout: { name: 'cose', animate: false, nodeRepulsion: 8000, idealEdgeLength: 90, randomize: false },
    });
    cyRef.current = cy;
    return () => cy.destroy();
  }, [topology]);

  return <div ref={ref} className="network" aria-label="测点拓扑图" />;
}

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class Edge:
    id: str
    from_id: str
    to_id: str
    value: float
    distance_km: float


@dataclass
class Component:
    index: int
    point_ids: list[str]
    observation_ids: list[str]
    datum_point_ids: list[str]
    edges: list[Edge]


def connected_components(point_ids: list[str], edges: list[Edge], datum_point_ids: set[str]) -> list[Component]:
    adj: dict[str, list[tuple[str, Edge]]] = {pid: [] for pid in point_ids}
    for edge in edges:
        if edge.from_id not in adj or edge.to_id not in adj:
            raise ValueError(f"observation {edge.id} references unknown endpoint")
        adj[edge.from_id].append((edge.to_id, edge))
        adj[edge.to_id].append((edge.from_id, edge))

    seen: set[str] = set()
    components: list[Component] = []
    index = 0
    for root in point_ids:
        if root in seen:
            continue
        stack = [root]
        pts: list[str] = []
        obs: set[str] = set()
        edge_by_id: dict[str, Edge] = {}
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            pts.append(node)
            for nxt, edge in adj[node]:
                if edge.id not in obs:
                    obs.add(edge.id)
                    edge_by_id[edge.id] = edge
                if nxt not in seen:
                    stack.append(nxt)
        pset = set(pts)
        comp_edges = [e for e in edge_by_id.values() if e.from_id in pset and e.to_id in pset]
        components.append(
            Component(
                index=index,
                point_ids=sorted(pts),
                observation_ids=sorted(obs),
                datum_point_ids=sorted(datum_point_ids & pset),
                edges=sorted(comp_edges, key=lambda e: e.id),
            )
        )
        index += 1
    return components


def cycle_basis_closures(components: list[Component], tolerance_factor: float = 0.006) -> list[dict]:
    """Independent fundamental cycle closures from a spanning tree.

    Build tree potentials once per component. Each non-tree edge yields a cycle
    closure in constant time, making the total work O(V+E).
    """

    cycles: list[dict] = []
    for comp in components:
        adj: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
        for e in comp.edges:
            adj[e.from_id].append((e.to_id, e.id, e.value))
            adj[e.to_id].append((e.from_id, e.id, -e.value))

        if not comp.point_ids:
            continue
        root = comp.point_ids[0]
        parent = {root: (None, None)}
        potential = {root: 0.0}
        order = [root]
        for node in order:
            for nxt, edge_id, signed_value in adj[node]:
                if nxt in parent:
                    continue
                parent[nxt] = (node, edge_id)
                potential[nxt] = potential[node] + signed_value
                order.append(nxt)

        tree_edges = {edge_id for _, edge_id in parent.values() if edge_id}
        for edge in comp.edges:
            if edge.id in tree_edges:
                continue
            closure = edge.value - potential[edge.to_id] + potential[edge.from_id]
            tolerance = tolerance_factor * (edge.distance_km ** 0.5)
            cycles.append(
                {
                    "component_index": comp.index,
                    "point_ids": [edge.from_id, edge.to_id],
                    "raw_closure_m": closure,
                    "adjusted_closure_m": None,
                    "tolerance_m": tolerance,
                    "passed": abs(closure) <= tolerance,
                    "edge_ids": [edge.id],
                    "chord_id": edge.id,
                }
            )
    return cycles

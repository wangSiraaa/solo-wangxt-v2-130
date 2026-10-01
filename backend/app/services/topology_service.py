from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.topology import Edge, connected_components
from ..models import DatumRule, Observation, Point


def working_topology(db: Session, project_id) -> dict:
    points = db.scalars(select(Point).where(Point.project_id == project_id).order_by(Point.code)).all()
    point_map = {p.id: p for p in points}
    observations = db.scalars(
        select(Observation).where(Observation.project_id == project_id, Observation.is_active.is_(True))
    ).all()
    datums = db.scalars(select(DatumRule).where(DatumRule.project_id == project_id, DatumRule.is_active.is_(True))).all()
    datum_points = {d.point_id for d in datums}

    edges = []
    for o in observations:
        mean = 0.5 * (o.raw_forward - o.raw_backward)
        edges.append(Edge(str(o.id), str(o.from_point_id), str(o.to_point_id), mean, o.distance_km))
    components = connected_components([str(p.id) for p in points], edges, {str(x) for x in datum_points})

    nodes = [
        {
            "data": {
                "id": str(p.id),
                "label": p.code,
                "is_datum": p.id in datum_points,
            },
            "position": {"x": (hash(p.code) % 800), "y": (hash(p.code + "y") % 600)},
        }
        for p in points
    ]
    edge_out = [
        {
            "data": {
                "id": str(o.id),
                "source": str(o.from_point_id),
                "target": str(o.to_point_id),
                "label": f"{o.line_code}: {0.5 * (o.raw_forward - o.raw_backward):.4f}",
                "raw_forward": o.raw_forward,
                "raw_backward": o.raw_backward,
            }
        }
        for o in observations
    ]
    comps = [
        {
            "index": c.index,
            "point_count": len(c.point_ids),
            "observation_count": len(c.observation_ids),
            "datum_count": len(c.datum_point_ids),
            "point_ids": c.point_ids,
        }
        for c in components
    ]
    return {"nodes": nodes, "edges": edge_out, "components": comps}

from __future__ import annotations

from dataclasses import dataclass

from .snapshot import DatumRecord, NetworkSnapshotData
from .topology import Component, cycle_basis_closures
from .weights import observation_misclosure, sigma_metres


@dataclass
class ComponentQC:
    component_index: int
    issues: list[dict]
    stats: dict
    cycles: list[dict]


def inspect_component(component: Component, snapshot: NetworkSnapshotData) -> ComponentQC:
    issues: list[dict] = []
    obs = {o.id: o for o in snapshot.observations}
    datums = {d.point_id: d for d in snapshot.datums}
    point_set = set(component.point_ids)

    if not component.datum_point_ids:
        issues.append(
            {
                "level": "error",
                "code": "UNCONSTRAINED_COMPONENT",
                "message": f"Component {component.index} has no fixed or weighted datum; heights are rank-deficient.",
                "component_index": component.index,
                "ids": component.point_ids[:20],
            }
        )

    fixed = [pid for pid in component.datum_point_ids if datums[pid].datum_type == "fixed"]
    if len(fixed) > 1:
        issues.append(
            {
                "level": "warning",
                "code": "MULTIPLE_FIXED_DATUMS",
                "message": "Multiple exact fixed datums can create contradictory constraints; residuals are explicitly audited.",
                "component_index": component.index,
                "ids": fixed,
            }
        )

    weighted_dupes = [d.point_id for d in snapshot.datums if d.point_id in point_set and d.datum_type == "weighted"]
    # Duplicate logical datums are rejected upstream; this remains defensive.
    if len(set(weighted_dupes)) != len(weighted_dupes):
        issues.append({"level": "error", "code": "DUPLICATE_DATUM", "message": "Duplicate datum rule", "component_index": component.index})

    misclosures = []
    for edge in component.edges:
        rec = obs[edge.id]
        m = observation_misclosure(rec)
        sigma = sigma_metres(rec, snapshot.weight_rules)
        misclosures.append(m)
        if abs(m) > 4 * sigma:
            issues.append(
                {
                    "level": "warning",
                    "code": "FORWARD_BACKWARD_MISCLOSURE",
                    "message": f"Observation {rec.line_code}/{rec.sequence} run misclosure {m:.6f} m exceeds 4 sigma.",
                    "component_index": component.index,
                    "ids": [rec.id],
                }
            )

    cycles = cycle_basis_closures([component])
    bad_cycles = [c for c in cycles if not c["passed"]]
    for cycle in bad_cycles:
        issues.append(
            {
                "level": "warning",
                "code": "RAW_LOOP_CLOSURE",
                "message": "Raw independent loop exceeds configured tolerance.",
                "component_index": component.index,
                "ids": cycle["edge_ids"],
            }
        )

    stats = {
        "point_count": len(component.point_ids),
        "observation_count": len(component.observation_ids),
        "datum_count": len(component.datum_point_ids),
        "max_abs_misclosure_m": max((abs(x) for x in misclosures), default=0.0),
        "cycle_count": len(cycles),
        "failed_cycle_count": len(bad_cycles),
    }
    return ComponentQC(component.index, issues, stats, cycles)

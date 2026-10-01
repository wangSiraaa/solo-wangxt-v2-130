from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PointRecord:
    id: str
    code: str
    x: float | None
    y: float | None


@dataclass(frozen=True)
class ObservationRecord:
    id: str
    stable_key: str
    line_code: str
    sequence: int
    from_id: str
    to_id: str
    raw_forward: float
    raw_backward: float
    distance_km: float
    sigma_add_mm: float | None
    sigma_per_km_mm: float | None


@dataclass(frozen=True)
class DatumRecord:
    id: str
    point_id: str
    datum_type: str
    elevation_m: float
    sigma_m: float | None


@dataclass(frozen=True)
class WeightRules:
    sigma_add_mm: float
    sigma_per_km_mm: float
    forward_backward_factor: float
    reject_min_distance_km: float | None
    custom_weights: dict[str, float]


@dataclass(frozen=True)
class NetworkSnapshotData:
    project_code: str
    points: list[PointRecord]
    observations: list[ObservationRecord]
    datums: list[DatumRecord]
    weight_rules: WeightRules
    algorithm_params: dict
    point_code_by_id: dict[str, str]

    def stable_payload(self) -> dict:
        def point_code(point_id: str) -> str:
            return self.point_code_by_id.get(point_id, point_id)

        custom_weights = {
            next(o.stable_key for o in self.observations if o.id == obs_id): value
            for obs_id, value in self.weight_rules.custom_weights.items()
        }
        weight_rules = asdict(self.weight_rules)
        weight_rules["custom_weights"] = custom_weights
        observations = [
            {
                "line_code": o.line_code,
                "sequence": o.sequence,
                "from_code": point_code(o.from_id),
                "to_code": point_code(o.to_id),
                "raw_forward": o.raw_forward,
                "raw_backward": o.raw_backward,
                "distance_km": o.distance_km,
                "sigma_add_mm": o.sigma_add_mm,
                "sigma_per_km_mm": o.sigma_per_km_mm,
            }
            for o in sorted(self.observations, key=lambda x: (x.line_code, x.sequence, x.from_id, x.to_id))
        ]
        datums = [
            {
                "point_code": point_code(d.point_id),
                "datum_type": d.datum_type,
                "elevation_m": d.elevation_m,
                "sigma_m": d.sigma_m,
            }
            for d in sorted(self.datums, key=lambda x: point_code(x.point_id))
        ]
        points = [
            {"code": p.code, "x": p.x, "y": p.y}
            for p in sorted(self.points, key=lambda x: x.code)
        ]
        return {
            "project_code": self.project_code,
            "points": points,
            "observations": observations,
            "datums": datums,
            "weight_rules": weight_rules,
            "algorithm_params": _stable(self.algorithm_params),
        }

    def stable_json(self) -> str:
        return json.dumps(_stable(self.stable_payload()), sort_keys=True, separators=(",", ":"), allow_nan=False)

    def canonical_json(self) -> str:
        # Persist both stable source content and internal UUID join mapping.
        # Reproducibility/hashing uses stable_json only.
        internal = {
            "points": [asdict(p) for p in self.points],
            "observations": [asdict(o) for o in self.observations],
            "datums": [asdict(d) for d in self.datums],
        }
        return json.dumps(
            {"stable": _stable(self.stable_payload()), "internal": _stable(internal)},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def input_sha256(self) -> str:
        return hashlib.sha256(self.stable_json().encode("utf-8")).hexdigest()

    def partial_hashes(self) -> tuple[str, str, str]:
        def h(value: object) -> str:
            return hashlib.sha256(json.dumps(_stable(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

        payload = self.stable_payload()
        return (h(payload["observations"]), h(payload["datums"]), h(payload["weight_rules"]))


def _stable(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _stable(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_stable(v) for v in value]
    return value

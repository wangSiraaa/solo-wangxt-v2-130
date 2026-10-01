import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.snapshot import DatumRecord, ObservationRecord, PointRecord, WeightRules, NetworkSnapshotData
from app.core.solver import solve_network


def load(path: str):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    point_codes = sorted({r["from_code"] for r in rows} | {r["to_code"] for r in rows})
    point_index = {code: f"p-{i:06d}" for i, code in enumerate(point_codes)}
    points = [PointRecord(point_index[code], code, None, None) for code in point_codes]
    observations = [
        ObservationRecord(
            id=f"o-{i:06d}", stable_key=f"{r['line_code']}#{int(r['sequence'])}", line_code=r["line_code"], sequence=int(r["sequence"]),
            from_id=point_index[r["from_code"]], to_id=point_index[r["to_code"]],
            raw_forward=float(r["raw_forward"]), raw_backward=float(r["raw_backward"]),
            distance_km=float(r["distance_km"]),
            sigma_add_mm=float(r["sigma_add_mm"]) if r.get("sigma_add_mm") else None,
            sigma_per_km_mm=float(r["sigma_per_km_mm"]) if r.get("sigma_per_km_mm") else None,
        )
        for i, r in enumerate(rows)
    ]
    datums = [
        DatumRecord("d-start", point_index["BM-START"], "fixed", 5.0, None),
        DatumRecord("d-iso", point_index["IS-0"], "fixed", 8.0, None),
    ]
    rules = WeightRules(1.0, 2.0, 1.0, None, {})
    params = {
        "dense_qr_limit": 5000, "condition_warning": 1e10, "condition_failure": 1e14,
        "svd_rank_eps": 1e-10, "qr_rank_tol": 1e-10, "regularization": "forbidden",
    }
    return NetworkSnapshotData("PRESSURE", points, observations, datums, rules, params, {v: k for k, v in point_index.items()})


if __name__ == "__main__":
    data = load(sys.argv[1] if len(sys.argv) > 1 else "pressure_100k.csv")
    t = time.perf_counter()
    result = solve_network(data)
    elapsed = time.perf_counter() - t
    print("ok", result.ok, "method", result.method, "points", len(data.points), "obs", len(data.observations), "seconds", round(elapsed, 3))
    print("dof", result.degrees_of_freedom, "sigma0", result.sigma0, "condition", result.condition_estimate)
    assert result.ok

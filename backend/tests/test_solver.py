from app.core.snapshot import DatumRecord, NetworkSnapshotData, ObservationRecord, PointRecord, WeightRules
from app.core.solver import solve_network


def _network(datum_elevation=5.0, weighted=False, datum_sigma=None):
    points = [PointRecord("a", "A", None, None), PointRecord("b", "B", None, None), PointRecord("c", "C", None, None)]
    obs = [
        ObservationRecord("o1", "L1#1", "L1", 1, "a", "b", 1.0, -1.0, 1.0, 0.001, 0.002),
        ObservationRecord("o2", "L2#1", "L2", 1, "b", "c", 0.5, -0.5, 1.0, 0.001, 0.002),
    ]
    datum = DatumRecord("d1", "a", "weighted" if weighted else "fixed", datum_elevation, datum_sigma)
    rules = WeightRules(0.001, 0.002, 1.0, None, {})
    params = {"dense_qr_limit": 5000, "condition_warning": 1e10, "condition_failure": 1e14, "svd_rank_eps": 1e-10, "qr_rank_tol": 1e-10}
    ids = {"a": "A", "b": "B", "c": "C"}
    return NetworkSnapshotData("P", points, obs, [datum], rules, params, ids)


def test_qr_diagnostic_returns_unique_height_without_regularization():
    result = solve_network(_network(), requested_method="qr")
    assert result.ok
    assert result.method == "qr_diagnostic"
    assert result.rank == 2


def test_unconstrained_network_refuses_to_invent_unique_heights():
    data = _network()
    data = NetworkSnapshotData(data.project_code, data.points, data.observations, [], data.weight_rules, data.algorithm_params, data.point_code_by_id)
    result = solve_network(data, requested_method="qr")
    assert not result.ok
    assert result.error_code == "RANK_DEFICIENT"
    assert result.heights == {}
    assert result.nullspace is not None

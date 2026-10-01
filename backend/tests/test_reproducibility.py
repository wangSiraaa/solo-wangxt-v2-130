from app.core.snapshot import DatumRecord, NetworkSnapshotData, ObservationRecord, PointRecord, WeightRules


def test_input_hash_ignores_internal_uuids_but_captures_rules_and_algorithm():
    points_a = [PointRecord("uuid-a", "A", None, None), PointRecord("uuid-b", "B", None, None)]
    points_b = [PointRecord("different-a", "A", None, None), PointRecord("different-b", "B", None, None)]
    obs_a = [ObservationRecord("obs-uuid-a", "L1#1", "L1", 1, "uuid-a", "uuid-b", 1.0, -1.0, 1.0, 1.0, 2.0)]
    obs_b = [ObservationRecord("another-uuid", "L1#1", "L1", 1, "different-a", "different-b", 1.0, -1.0, 1.0, 1.0, 2.0)]
    datums_a = [DatumRecord("datum-uuid", "uuid-a", "fixed", 5.0, None)]
    datums_b = [DatumRecord("datum-other", "different-a", "fixed", 5.0, None)]
    rules = WeightRules(1.0, 2.0, 1.0, None, {"obs-uuid-a": 3.0})
    rules_b = WeightRules(1.0, 2.0, 1.0, None, {"another-uuid": 3.0})
    params = {"regularization": "forbidden", "method": "sparse_normal_equations"}

    a = NetworkSnapshotData("P", points_a, obs_a, datums_a, rules, params, {"uuid-a": "A", "uuid-b": "B"})
    b = NetworkSnapshotData("P", points_b, obs_b, datums_b, rules_b, params, {"different-a": "A", "different-b": "B"})
    assert a.input_sha256() == b.input_sha256()

    changed = NetworkSnapshotData("P", points_b, obs_b, datums_b, WeightRules(1.0, 3.0, 1.0, None, {}), params, {"different-a": "A", "different-b": "B"})
    assert changed.input_sha256() != a.input_sha256()

from pathlib import Path

from conftest import run_job_sync


def test_snapshot_adjustment_optimistic_lock_and_raw_preservation(client):
    project = client.post("/api/projects", json={"code": "P1", "name": "sample"}).json()
    csv_path = Path(__file__).parents[2] / "scripts" / "sample_network.csv"
    with csv_path.open("rb") as f:
        imported = client.post(
            f"/api/projects/{project['id']}/import",
            files={"file": ("sample.csv", f, "text/csv")},
        ).json()
    assert imported["created_observations"] == 6

    topology = client.get(f"/api/projects/{project['id']}/topology").json()
    assert len(topology["components"]) == 2
    assert sorted(c["datum_count"] for c in topology["components"]) == [0, 0]

    datum = client.post(
        f"/api/projects/{project['id']}/datums",
        json={"point_code": "BM-A", "datum_type": "fixed", "elevation_m": 10.0},
    )
    assert datum.status_code == 200
    project = client.get(f"/api/projects/{project['id']}").json()

    # Repeated submission of unchanged draft/snapshot returns one job, not a new generation.
    payload = {"expected_draft_version": project["draft_version"]}
    first = client.post(f"/api/projects/{project['id']}/jobs", json=payload).json()
    second = client.post(f"/api/projects/{project['id']}/jobs", json=payload).json()
    assert first["id"] == second["id"]

    finished = run_job_sync(client, project["id"], first["id"])
    assert finished["status"] == "failed"
    # Isolated subnet has no datum: partition is parallel prechecked, global solve rejects rank loss.
    result = client.get(f"/api/jobs/{first['id']}/result").json()
    assert result["nullspace"] is not None
    assert any(issue["code"] == "UNCONSTRAINED_COMPONENT" for comp in client.get(f"/api/jobs/{first['id']}/components").json() for issue in comp["issues"])

    # Fix only the connected main component; isolated component still makes publication impossible.
    # Create a constrained two-component project by adding datum to isolated point instead.
    client.post(
        f"/api/projects/{project['id']}/datums",
        json={"point_code": "ISO-1", "datum_type": "fixed", "elevation_m": 20.0},
    )
    project = client.get(f"/api/projects/{project['id']}").json()
    job2 = client.post(f"/api/projects/{project['id']}/jobs", json={"expected_draft_version": project["draft_version"]}).json()
    finished2 = run_job_sync(client, project["id"], job2["id"])
    assert finished2["status"] == "ready"
    result2 = client.get(f"/api/jobs/{job2['id']}/result").json()
    assert result2["method"] == "sparse_normal_equations"
    assert result2["audit"]["passed"] is True
    assert result2["audit"]["closure_stats"]["max_abs_adjusted_closure_m"] < 1e-3

    # Original raw observations remain exactly imported and differ from adjusted output.
    obs = client.get(f"/api/projects/{project['id']}/observations", params={"limit": 10}).json()
    by_line = {r["line_code"]: r for r in obs}
    res_by_line = {r["line_code"]: r for r in result2["residuals"]}
    assert by_line["L1"]["raw_forward"] == 1.2503
    assert res_by_line["L1"]["adjusted_delta_m"] != by_line["L1"]["raw_forward"]

    # Weight revision uses optimistic lock; stale revision gets HTTP 409 and does not alter draft.
    weights = client.get(f"/api/projects/{project['id']}/weights").json()
    stale = client.put(
        f"/api/projects/{project['id']}/weights",
        json={**weights, "sigma_per_km_mm": 3.0, "expected_version": weights["lock_version"] + 99},
    )
    assert stale.status_code == 409
    updated = client.put(
        f"/api/projects/{project['id']}/weights",
        json={**weights, "sigma_per_km_mm": 3.0, "expected_version": weights["lock_version"]},
    ).json()
    assert updated["lock_version"] == weights["lock_version"] + 1
    project = client.get(f"/api/projects/{project['id']}").json()

    # Old ready job remains auditable but can never publish after a newer draft revision.
    publish_old = client.post(
        f"/api/projects/{project['id']}/publish",
        json={"expected_draft_version": project["draft_version"]},
    )
    assert publish_old.status_code == 409

    job3 = client.post(f"/api/projects/{project['id']}/jobs", json={"expected_draft_version": project["draft_version"]}).json()
    assert job3["generation"] == job2["generation"] + 1
    run_job_sync(client, project["id"], job3["id"])
    published = client.post(
        f"/api/projects/{project['id']}/publish",
        json={"expected_draft_version": project["draft_version"]},
    )
    assert published.status_code == 200
    publication = published.json()
    assert publication["input_sha256"]
    assert publication["algorithm_params"]["regularization"] == "forbidden"



def test_revision_marks_running_old_generation_audit_only(client):
    project = client.post("/api/projects", json={"code": "SUPERSEDE", "name": "supersede"}).json()
    csv = (
        "line_code,sequence,from_code,to_code,raw_forward,raw_backward,distance_km,sigma_add_mm,sigma_per_km_mm\n"
        "L1,1,A,B,1.000,-1.000,1,1,2\nL2,1,B,C,1.000,-1.000,1,1,2\n"
        "LX,1,X,Y,0.300,-0.300,1,1,2\n"
    )
    client.post(f"/api/projects/{project['id']}/import", files={"file": ("s.csv", csv, "text/csv")})
    client.post(f"/api/projects/{project['id']}/datums", json={"point_code": "A", "datum_type": "fixed", "elevation_m": 0})
    client.post(f"/api/projects/{project['id']}/datums", json={"point_code": "X", "datum_type": "fixed", "elevation_m": 2})
    project = client.get(f"/api/projects/{project['id']}").json()
    old = client.post(f"/api/projects/{project['id']}/jobs", json={"expected_draft_version": project["draft_version"]}).json()
    client.post(f"/api/jobs/{old['id']}/advance")
    weights = client.get(f"/api/projects/{project['id']}/weights").json()
    updated = client.put(f"/api/projects/{project['id']}/weights", json={**weights, "sigma_per_km_mm": 4.0, "expected_version": weights["lock_version"]}).json()
    assert updated["lock_version"] == weights["lock_version"] + 1
    old = client.get(f"/api/jobs/{old['id']}").json()
    assert old["status"] == "running"
    assert old["stage"] in {"components", "partition_qc", "global_solve"}
    components = client.get(f"/api/jobs/{old['id']}/components").json()
    for component in components:
        client.post(f"/api/jobs/{old['id']}/run-component/{component['id']}")
    client.post(f"/api/jobs/{old['id']}/solve")
    old = client.get(f"/api/jobs/{old['id']}").json()
    assert old["status"] == "superseded"
    # Old immutable result/job remains queryable and audit-passed, but cannot be published.
    project = client.get(f"/api/projects/{project['id']}").json()
    new = client.post(f"/api/projects/{project['id']}/jobs", json={"expected_draft_version": project["draft_version"]}).json()
    assert new["generation"] == old["generation"] + 1


def test_multiple_fixed_datums_are_explicitly_warned(client):
    project = client.post("/api/projects", json={"code": "DATUM", "name": "datum conflict"}).json()
    csv = (
        "line_code,sequence,from_code,to_code,raw_forward,raw_backward,distance_km,sigma_add_mm,sigma_per_km_mm\n"
        "L1,1,A,B,1.000,-1.000,1,1,2\n"
        "L2,1,B,C,2.000,-2.000,1,1,2\n"
    )
    client.post(f"/api/projects/{project['id']}/import", files={"file": ("d.csv", csv, "text/csv")})
    client.post(f"/api/projects/{project['id']}/datums", json={"point_code": "A", "datum_type": "fixed", "elevation_m": 0})
    client.post(f"/api/projects/{project['id']}/datums", json={"point_code": "C", "datum_type": "fixed", "elevation_m": 5})
    project = client.get(f"/api/projects/{project['id']}").json()
    job = client.post(f"/api/projects/{project['id']}/jobs", json={"expected_draft_version": project["draft_version"]}).json()
    finished = run_job_sync(client, project["id"], job["id"])
    # Contradiction with both exact fixed points is detected in QC warning; WLS solves and
    # audit reports residual stats. Hard constraints are not silently relaxed by a ridge term.
    components = client.get(f"/api/jobs/{job['id']}/components").json()
    assert any(issue["code"] == "MULTIPLE_FIXED_DATUMS" for c in components for issue in c["issues"])
    assert finished["status"] in {"ready", "failed"}

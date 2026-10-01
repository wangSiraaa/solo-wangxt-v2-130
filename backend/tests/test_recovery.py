import uuid

from sqlalchemy import select

from conftest import run_job_sync


def test_interrupted_job_resumes_from_confirmed_partition_stage(client):
    p = client.post("/api/projects", json={"code": "RECOVER", "name": "recovery"}).json()
    csv = (
        "line_code,sequence,from_code,to_code,raw_forward,raw_backward,distance_km,sigma_add_mm,sigma_per_km_mm\n"
        "L1,1,A,B,1.000,-1.000,1,1,2\nL2,1,B,C,1.000,-1.000,1,1,2\n"
    )
    client.post(f"/api/projects/{p['id']}/import", files={"file": ("r.csv", csv, "text/csv")})
    client.post(f"/api/projects/{p['id']}/datums", json={"point_code": "A", "datum_type": "fixed", "elevation_m": 0})
    project = client.get(f"/api/projects/{p['id']}").json()
    job = client.post(f"/api/projects/{p['id']}/jobs", json={"expected_draft_version": project["draft_version"]}).json()

    # Simulate worker shutdown after component discovery and one partition QC item.
    client.post(f"/api/jobs/{job['id']}/advance")
    components = client.get(f"/api/jobs/{job['id']}/components").json()
    assert len(components) == 1
    client.post(f"/api/jobs/{job['id']}/run-component/{components[0]['id']}")

    # Repeated scheduler ticks must not duplicate partition outputs or the job.
    client.post(f"/api/jobs/{job['id']}/advance")
    duplicate = client.post(f"/api/projects/{p['id']}/jobs", json={"expected_draft_version": project["draft_version"]}).json()
    assert duplicate["id"] == job["id"]

    finished = run_job_sync(client, p["id"], job["id"])
    assert finished["confirmed_stage"] in {"audit", "ready"}
    assert finished["status"] == "ready"

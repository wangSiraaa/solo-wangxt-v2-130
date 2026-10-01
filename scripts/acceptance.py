#!/usr/bin/env python3
"""End-to-end acceptance driver for API-level scenarios.

Usage:
  python scripts/acceptance.py http://localhost:8000 scripts/sample_network.csv
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx


def wait_job(client: httpx.Client, job_id: str) -> dict:
    while True:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"ready", "failed", "superseded"}:
            return job
        client.post(f"/api/jobs/{job_id}/advance")
        # If celery workers are absent, execute partition tasks through the API.
        comps = client.get(f"/api/projects/{job['project_id']}/topology").json()["components"]
        # Components are represented in DB but endpoint listing is not exposed;
        # the scheduler retries until worker or test worker processes them.
        time.sleep(0.5)


def main(base: str, csv_path: str) -> None:
    with httpx.Client(base_url=base, timeout=60) as client:
        code = f"ACC-{int(time.time())}"
        p = client.post("/api/projects", json={"code": code, "name": "验收项目"}).json()
        with Path(csv_path).open("rb") as f:
            imported = client.post(f"/api/projects/{p['id']}/import", files={"file": (Path(csv_path).name, f)}).json()
        print("import", imported)
        datum = client.post(f"/api/projects/{p['id']}/datums", json={"point_code": "BM-A", "datum_type": "fixed", "elevation_m": 10.0})
        print("datum", datum.status_code)
        version = client.get(f"/api/projects/{p['id']}").json()["draft_version"]
        job = client.post(f"/api/projects/{p['id']}/jobs", json={"expected_draft_version": version}).json()
        print("job", job)
        finished = wait_job(client, job["id"])
        print("finished", finished["status"], finished["stage"], finished.get("error_code"))
        if finished["status"] == "ready":
            pub = client.post(f"/api/projects/{p['id']}/publish", json={"expected_draft_version": version})
            print("publish", pub.status_code, pub.text[:500])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000", sys.argv[2] if len(sys.argv) > 2 else "scripts/sample_network.csv")

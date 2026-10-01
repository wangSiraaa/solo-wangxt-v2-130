import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def client(monkeypatch):
    db_path = Path(tempfile.mkdtemp()) / "test.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from app import database
    from app.api import routes
    from app.main import app

    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", TestingSessionLocal)

    from app.workers import tasks as worker_tasks
    monkeypatch.setattr(worker_tasks, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(worker_tasks, "_safe_delay", lambda task, *args: None)
    worker_tasks.celery_app.conf.task_always_eager = True
    worker_tasks.celery_app.conf.task_eager_propagates = False
    worker_tasks.advance_jobs.delay = lambda *args, **kwargs: None

    from functools import partial

    app.dependency_overrides[routes.get_db] = partial(_override, TestingSessionLocal)
    database.Base.metadata.create_all(bind=engine)

    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    engine.dispose()


def _override(session_local):
    db = session_local()
    try:
        yield db
    finally:
        db.close()


def run_job_sync(client, project_id: str, job_id: str):
    client.post(f"/api/jobs/{job_id}/advance")
    components = client.get(f"/api/jobs/{job_id}/components").json()
    for component in components:
        client.post(f"/api/jobs/{job_id}/run-component/{component['id']}")
    response = client.post(f"/api/jobs/{job_id}/solve")
    assert response.status_code in (200, 422), response.text
    return client.get(f"/api/jobs/{job_id}").json()

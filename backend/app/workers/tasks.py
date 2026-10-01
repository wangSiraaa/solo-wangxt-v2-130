from __future__ import annotations

import uuid
from datetime import timedelta

from celery import chain
from sqlalchemy import select

from ..database import SessionLocal
from ..models import (
    Job,
    JobComponent,
    JobStage,
    JobStatus,
)
from ..services import job_runner
from ..services.snapshots import load_snapshot_data
from .celery_app import celery_app

STALL_AFTER = timedelta(minutes=5)


def _safe_delay(task, *args) -> None:
    # API processes must still record durable state when Redis is unavailable;
    # recovery tick/worker will pick the same idempotent work item up later.
    try:
        task.apply_async(args=args)
    except Exception:
        pass


@celery_app.task(name="app.workers.tasks.advance_jobs")
def advance_jobs() -> dict:
    """Idempotent scheduler/recovery entrypoint.

    A beat tick (or explicit API call in tests) inspects durable state and only
    dispatches unfinished partitions. Confirmed stages are never repeated.
    """

    db = SessionLocal()
    try:
        from datetime import datetime, timezone

        jobs = db.scalars(
            select(Job).where(Job.status.in_([JobStatus.queued, JobStatus.running])).order_by(Job.created_at)
        ).all()
        actions = []
        for job in jobs:
            if job.status == JobStatus.queued or job.confirmed_stage == JobStage.accepted:
                snapshot = job.snapshot
                data = load_snapshot_data(db, snapshot)
                rows = job_runner.create_components(db, job, data)
                for row in rows:
                    _safe_delay(run_component_qc, str(job.id), str(row.id))
                actions.append({"job": str(job.id), "components": len(rows)})
                continue

            if job.stage == JobStage.partition_qc:
                rows = db.scalars(select(JobComponent).where(JobComponent.job_id == job.id)).all()
                pending = [r for r in rows if r.status == "pending"]
                stale = False
                if job.heartbeat_at is not None:
                    heartbeat = job.heartbeat_at
                    if heartbeat.tzinfo is None:
                        from datetime import timezone as _tz

                        heartbeat = heartbeat.replace(tzinfo=_tz.utc)
                    stale = datetime.now(timezone.utc) - heartbeat > STALL_AFTER
                for row in pending:
                    _safe_delay(run_component_qc, str(job.id), str(row.id))
                if not rows:
                    continue
                if job_runner.all_components_confirmed(db, job):
                    job_runner.checkpoint(db, job, JobStage.partition_qc, {"components": len(rows)})
                    job.stage = JobStage.global_solve
                    db.commit()
                    _safe_delay(run_global_solve, str(job.id))
                    actions.append({"job": str(job.id), "global_solve": True})
                elif stale:
                    for row in rows:
                        if row.status in ("pending", "running"):
                            _safe_delay(run_component_qc, str(job.id), str(row.id))
                continue

            if job.stage == JobStage.global_solve:
                # Restart either before the unconfirmed solve or between durable
                # global result and final audit confirmation.
                _safe_delay(run_global_solve, str(job.id))
        return {"advanced": actions}
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.run_component_qc", bind=True, max_retries=3)
def run_component_qc(self, job_id: str, component_id: str) -> dict:
    db = SessionLocal()
    try:
        component = db.get(JobComponent, uuid.UUID(component_id))
        if component and component.job.status == JobStatus.superseded:
            component.status = "skipped_superseded"
            db.commit()
            return {"idempotent": True, "superseded": True}
        if component and component.status not in ("pending", "running"):
            return {"idempotent": True, "status": component.status}
        if component:
            component.status = "running"
            job = db.get(Job, uuid.UUID(job_id))
            if job:
                from datetime import datetime, timezone

                job.heartbeat_at = datetime.now(timezone.utc)
            db.commit()
        try:
            return job_runner.run_component_qc(db, uuid.UUID(job_id), uuid.UUID(component_id))
        except Exception as exc:
            if component:
                component.status = "pending"
                db.commit()
            raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1))
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.run_global_solve", bind=True, max_retries=1)
def run_global_solve(self, job_id: str) -> dict:
    db = SessionLocal()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        if not job:
            return {"missing": job_id}
        if job.status == JobStatus.ready:
            return {"idempotent": True, "ready": True}
        # Do not overwrite newer work. An old snapshot may finish for audit, but
        # its terminal status is superseded rather than current/ready.
        if job.status == JobStatus.superseded:
            return {"idempotent": True, "superseded": True}

        if not job_runner.all_components_confirmed(db, job):
            _safe_delay(advance_jobs)
            return {"waiting_for_components": True}

        job.status = JobStatus.running
        job.stage = JobStage.global_solve
        db.commit()
        try:
            result = job.result
            if result is None:
                result = job_runner.persist_solve(db, job)
                db.refresh(job)
            if result.nullspace:
                job_runner.fail_job(db, job, "RANK_DEFICIENT", result.nullspace.get("message", "rank deficient"), result)
                return {"ok": False, "rank_deficient": True}
            job_runner.checkpoint(db, job, JobStage.global_solve, {"result_id": str(result.id), "method": result.method})
            db.commit()
            audit = job_runner.audit_and_finish(db, job, result)
            return {"ok": audit["passed"], "audit": audit}
        except Exception as exc:
            db.rollback()
            job = db.get(Job, uuid.UUID(job_id))
            job_runner.fail_job(db, job, "SOLVE_EXCEPTION", str(exc))
            raise
    finally:
        db.close()

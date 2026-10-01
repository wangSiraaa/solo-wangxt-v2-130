from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditEvent, Job, JobStage, JobStatus, Project, Snapshot


def supersede_project_jobs(db: Session, project: Project, reason: str) -> int:
    """Record older-draft jobs as non-publishable after a draft revision.

    Durable results and audit rows are retained. A task that is already in
    flight may still complete; publish and ready transitions only accept a
    snapshot matching current draft_version, so it can never overwrite new work.
    """

    snapshots = db.scalars(select(Snapshot).where(Snapshot.project_id == project.id)).all()
    old_snapshot_ids = [s.id for s in snapshots if s.draft_version < project.draft_version]
    jobs = db.scalars(
        select(Job).where(Job.project_id == project.id, Job.snapshot_id.in_(old_snapshot_ids))
    ).all()
    count = 0
    for job in jobs:
        if job.status in {JobStatus.queued, JobStatus.running}:
            # Preserve running for completion/audit. Queued work that has not
            # started is immediately marked superseded.
            if job.status == JobStatus.queued:
                job.status = JobStatus.superseded
                job.stage = JobStage.superseded
            snapshot = db.get(Snapshot, job.snapshot_id)
            db.add(AuditEvent(
                project_id=project.id,
                job_id=job.id,
                event_type="JOB_RETIRED_BY_DRAFT_REVISION",
                detail={
                    "reason": reason,
                    "old_draft_version": snapshot.draft_version if snapshot else None,
                    "current_draft_version": project.draft_version,
                    "may_finish_audit": job.status == JobStatus.running,
                    "can_publish": False,
                },
            ))
            count += 1
    db.flush()
    return count

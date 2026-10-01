from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Job, JobStatus, Publication, Snapshot


class PublishError(RuntimeError):
    pass


def publish_current(db: Session, project, expected_draft_version: int) -> Publication:
    if project.draft_version != expected_draft_version:
        raise PublishError(f"draft_version conflict: expected {expected_draft_version}, current {project.draft_version}")
    job = db.scalar(
        select(Job)
        .where(Job.project_id == project.id, Job.status == JobStatus.ready)
        .order_by(Job.generation.desc())
    )
    if not job:
        raise PublishError("no ready job for current draft")
    snapshot = db.get(Snapshot, job.snapshot_id)
    if snapshot.draft_version != project.draft_version:
        raise PublishError("ready job is superseded by a newer draft")
    audit = job.result.audit if job.result else {}
    if not audit.get("passed"):
        raise PublishError("job audit has not passed")

    latest = db.scalar(
        select(Publication).where(Publication.project_id == project.id).order_by(Publication.published_version.desc())
    )
    version = (latest.published_version + 1 if latest else 1)
    summary = {
        "method": job.result.method,
        "rank": job.result.rank,
        "degrees_of_freedom": job.result.degrees_of_freedom,
        "sigma0": job.result.sigma0,
        "weighted_rss": job.result.weighted_rss,
        "condition_estimate": job.result.condition_estimate,
        "audit": audit,
    }
    publication = Publication(
        project_id=project.id,
        job_id=job.id,
        snapshot_id=snapshot.id,
        published_version=version,
        input_sha256=snapshot.input_sha256,
        algorithm_params=snapshot.algorithm_params,
        summary=summary,
    )
    db.add(publication)
    db.commit()
    db.refresh(publication)
    return publication

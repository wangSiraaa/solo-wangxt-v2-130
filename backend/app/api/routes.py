from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DatumRule, Job, JobComponent, JobStage, Observation, Point, Project, WeightRule
from ..schemas import (
    DatumIn,
    DatumOut,
    DatumRevision,
    HeightOut,
    ImportResult,
    JobOut,
    JobSubmit,
    ObservationOut,
    ObservationRevision,
    PointOut,
    ProjectCreate,
    ProjectOut,
    PublicationOut,
    PublishRequest,
    ResidualOut,
    ResultOut,
    TopologyOut,
    WeightRuleIn,
    WeightRuleOut,
)
from ..services.publishing import PublishError, publish_current
from ..services.revisions import ConflictError, revise_datum, revise_observation, revise_weights
from ..services.snapshots import (
    add_datum,
    build_snapshot_data,
    get_or_create_project,
    import_rows,
    parse_csv,
    submit_job,
)
from ..services.topology_service import working_topology

router = APIRouter(prefix="/api")


def get_project_or_404(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    return project


@router.post("/projects", response_model=ProjectOut)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Project).where(Project.code == payload.code)):
        raise HTTPException(409, "project code already exists")
    project = get_or_create_project(db, payload.code, payload.name)
    db.commit()
    db.refresh(project)
    return project


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return db.scalars(select(Project).order_by(Project.created_at)).all()


@router.get("/projects/{project_id}", response_model=ProjectOut)
def read_project(project_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_project_or_404(db, project_id)


@router.post("/projects/{project_id}/import", response_model=ImportResult)
async def import_leveling(project_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    content = await file.read()
    try:
        rows = parse_csv(content)
        result = import_rows(db, project, rows)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    result["input_sha256"] = build_snapshot_data(db, project).input_sha256()
    return result


@router.get("/projects/{project_id}/points", response_model=list[PointOut])
def list_points(project_id: uuid.UUID, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    return db.scalars(select(Point).where(Point.project_id == project_id).order_by(Point.code)).all()


@router.get("/projects/{project_id}/observations", response_model=list[ObservationOut])
def list_observations(project_id: uuid.UUID, limit: int = 200, offset: int = 0, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    return db.scalars(
        select(Observation).where(Observation.project_id == project_id).order_by(Observation.line_code, Observation.sequence).limit(limit).offset(offset)
    ).all()


@router.patch("/projects/{project_id}/observations/{observation_id}", response_model=ObservationOut)
def patch_observation(project_id: uuid.UUID, observation_id: uuid.UUID, payload: ObservationRevision, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    obs = db.get(Observation, observation_id)
    if not obs or obs.project_id != project_id:
        raise HTTPException(404, "observation not found")
    try:
        revise_observation(db, project, obs, payload.model_dump(exclude_unset=True))
        db.commit()
    except ConflictError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return obs


@router.post("/projects/{project_id}/datums", response_model=DatumOut)
def create_datum(project_id: uuid.UUID, payload: DatumIn, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    try:
        datum = add_datum(db, project, payload.point_code, payload.datum_type, payload.elevation_m, payload.sigma_m)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return datum


@router.get("/projects/{project_id}/datums", response_model=list[DatumOut])
def list_datums(project_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(select(DatumRule).where(DatumRule.project_id == project_id)).all()


@router.patch("/projects/{project_id}/datums/{datum_id}", response_model=DatumOut)
def patch_datum(project_id: uuid.UUID, datum_id: uuid.UUID, payload: DatumRevision, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    datum = db.get(DatumRule, datum_id)
    if not datum or datum.project_id != project_id:
        raise HTTPException(404, "datum not found")
    try:
        revise_datum(db, project, datum, payload.model_dump(exclude_unset=True))
        db.commit()
    except ConflictError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return datum


@router.get("/projects/{project_id}/weights", response_model=WeightRuleOut)
def get_weights(project_id: uuid.UUID, db: Session = Depends(get_db)):
    rule = db.scalar(select(WeightRule).where(WeightRule.project_id == project_id))
    if not rule:
        raise HTTPException(404, "weight rule not found")
    return rule


@router.put("/projects/{project_id}/weights", response_model=WeightRuleOut)
def put_weights(project_id: uuid.UUID, payload: WeightRuleIn, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    rule = db.scalar(select(WeightRule).where(WeightRule.project_id == project_id))
    try:
        revise_weights(db, project, rule, payload.model_dump(exclude_unset=True))
        db.commit()
    except ConflictError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return rule


@router.get("/projects/{project_id}/topology", response_model=TopologyOut)
def topology(project_id: uuid.UUID, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    return working_topology(db, project_id)


@router.post("/projects/{project_id}/jobs", response_model=JobOut)
def create_job(project_id: uuid.UUID, payload: JobSubmit, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    try:
        job, created = submit_job(db, project, payload.expected_draft_version, payload.algorithm)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    # Scheduling through Celery is fire-and-forget. In tests/tiny deployments
    # call /advance to run the same idempotent state machine synchronously.
    try:
        from ..workers.tasks import advance_jobs

        advance_jobs.delay()
    except Exception:
        pass
    return job


@router.post("/jobs/{job_id}/advance")
def advance_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    """Run/schedule one recovery tick. Useful without celery beat and tests."""

    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    from ..workers import tasks

    tasks.advance_jobs()
    db.refresh(job)
    return JobOut.model_validate(job)


@router.post("/jobs/{job_id}/run-component/{component_id}")
def run_component(job_id: uuid.UUID, component_id: uuid.UUID, db: Session = Depends(get_db)):
    from ..services.job_runner import run_component_qc

    return run_component_qc(db, job_id, component_id)


@router.post("/jobs/{job_id}/solve")
def solve_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    from ..services.job_runner import all_components_confirmed, audit_and_finish, checkpoint, fail_job, persist_solve

    if not all_components_confirmed(db, job):
        raise HTTPException(409, "partition QC not confirmed")
    result = persist_solve(db, job)
    if result.nullspace:
        fail_job(db, job, "RANK_DEFICIENT", result.nullspace.get("message", "rank deficient"), result)
        raise HTTPException(422, {"error_code": "RANK_DEFICIENT", "nullspace": result.nullspace})
    checkpoint(db, job, JobStage.global_solve, {"method": result.method})
    db.commit()
    audit = audit_and_finish(db, job, result)
    return {"audit": audit, "job": JobOut.model_validate(job)}


@router.get("/projects/{project_id}/jobs", response_model=list[JobOut])
def list_jobs(project_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc())).all()


@router.get("/jobs/{job_id}", response_model=JobOut)
def read_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@router.get("/jobs/{job_id}/components")
def job_components(job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    rows = db.scalars(select(JobComponent).where(JobComponent.job_id == job.id).order_by(JobComponent.component_index)).all()
    return [
        {
            "id": str(r.id),
            "component_index": r.component_index,
            "status": r.status,
            "point_count": len(r.point_ids),
            "observation_count": len(r.observation_ids),
            "datum_count": len(r.datum_point_ids),
            "issues": r.issues,
            "stats": r.raw_closure_stats,
        }
        for r in rows
    ]


@router.get("/jobs/{job_id}/result", response_model=ResultOut)
def read_result(job_id: uuid.UUID, residual_limit: int = 200, height_limit: int = 200, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or not job.result:
        raise HTTPException(404, "result not found")
    points = {p.id: p for p in db.scalars(select(Point).where(Point.project_id == job.project_id)).all()}
    obs = {o.id: o for o in db.scalars(select(Observation).where(Observation.project_id == job.project_id)).all()}
    height_rows = sorted(job.result.point_heights, key=lambda h: h.point_id)[:max(0, height_limit)]
    heights = [
        HeightOut(
            point_id=h.point_id,
            point_code=points[h.point_id].code,
            adjusted_height_m=h.adjusted_height_m,
            datum_residual_m=h.datum_residual_m,
        )
        for h in height_rows
    ]
    residual_rows = sorted(job.result.observation_residuals, key=lambda r: abs(r.residual_m), reverse=True)[:max(0, residual_limit)]
    residuals = [
        ResidualOut(
            observation_id=r.observation_id,
            line_code=obs[r.observation_id].line_code,
            raw_mean=r.raw_mean,
            adjusted_delta_m=r.adjusted_delta_m,
            correction_m=r.correction_m,
            residual_m=r.residual_m,
            weight=r.weight,
        )
        for r in residual_rows
    ]
    return ResultOut(
        job=JobOut.model_validate(job),
        method=job.result.method,
        rank=job.result.rank,
        degrees_of_freedom=job.result.degrees_of_freedom,
        sigma0=job.result.sigma0,
        weighted_rss=job.result.weighted_rss,
        condition_estimate=job.result.condition_estimate,
        stats=job.result.stats,
        nullspace=job.result.nullspace,
        audit=job.result.audit,
        heights=heights,
        residuals=residuals,
    )


@router.get("/jobs/{job_id}/export/residuals.csv")
def export_residuals(job_id: uuid.UUID, db: Session = Depends(get_db)):
    import csv
    import io

    job = db.get(Job, job_id)
    if not job or not job.result:
        raise HTTPException(404, "result not found")
    point_map = {p.id: p.code for p in db.scalars(select(Point).where(Point.project_id == job.project_id)).all()}
    obs_map = {o.id: o for o in db.scalars(select(Observation).where(Observation.project_id == job.project_id)).all()}

    def rows():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["line_code", "sequence", "from_code", "to_code", "raw_forward_m", "raw_backward_m", "raw_mean_m", "adjusted_delta_m", "correction_m", "residual_m", "weight"])
        yield buffer.getvalue()
        for r in job.result.observation_residuals:
            obs = obs_map[r.observation_id]
            buffer.seek(0); buffer.truncate(0)
            writer.writerow([
                obs.line_code, obs.sequence, point_map[obs.from_point_id], point_map[obs.to_point_id],
                r.raw_forward, r.raw_backward, r.raw_mean, r.adjusted_delta_m, r.correction_m, r.residual_m, r.weight,
            ])
            yield buffer.getvalue()

    return StreamingResponse(rows(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="job-{job_id}-residuals.csv"'})


@router.post("/projects/{project_id}/publish", response_model=PublicationOut)
def publish(project_id: uuid.UUID, payload: PublishRequest, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    try:
        return publish_current(db, project, payload.expected_draft_version)
    except PublishError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/projects/{project_id}/publications", response_model=list[PublicationOut])
def publications(project_id: uuid.UUID, db: Session = Depends(get_db)):
    from ..models import Publication

    return db.scalars(
        select(Publication).where(Publication.project_id == project_id).order_by(Publication.published_version.desc())
    ).all()

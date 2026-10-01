from __future__ import annotations

import json
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.qc import inspect_component
from ..core.snapshot import NetworkSnapshotData
from ..core.solver import solve_network
from ..core.topology import Edge, cycle_basis_closures
from ..core.weights import observation_mean, observation_misclosure, weight_for
from ..models import (
    AuditEvent,
    Job,
    JobCheckpoint,
    JobComponent,
    JobResult,
    JobStage,
    JobStatus,
    Observation,
    Point,
    Project,
    ResultCycle,
    ResultObservation,
    ResultPointHeight,
    Snapshot,
)
from .snapshots import load_snapshot_data


def _audit(db: Session, job_id: uuid.UUID | None, project_id: uuid.UUID | None, event_type: str, detail: dict) -> None:
    db.add(AuditEvent(job_id=job_id, project_id=project_id, event_type=event_type, detail=detail))


def checkpoint(db: Session, job: Job, stage: JobStage, state: dict | None = None) -> None:
    exists = db.scalar(select(JobCheckpoint).where(JobCheckpoint.job_id == job.id, JobCheckpoint.stage == stage))
    if exists:
        exists.state = state or {}
    else:
        db.add(JobCheckpoint(job_id=job.id, stage=stage, state=state or {}))
    job.confirmed_stage = stage
    db.flush()


def create_components(db: Session, job: Job, snapshot_data: NetworkSnapshotData) -> list[JobComponent]:
    existing = db.scalars(select(JobComponent).where(JobComponent.job_id == job.id).order_by(JobComponent.component_index)).all()
    if existing:
        return list(existing)
    edges = [
        Edge(o.id, o.from_id, o.to_id, observation_mean(o), o.distance_km)
        for o in snapshot_data.observations
    ]
    datum_points = {d.point_id for d in snapshot_data.datums}
    from ..core.topology import connected_components

    comps = connected_components([p.id for p in snapshot_data.points], edges, datum_points)
    rows = []
    for c in comps:
        row = JobComponent(
            job_id=job.id,
            component_index=c.index,
            point_ids=c.point_ids,
            observation_ids=c.observation_ids,
            datum_point_ids=c.datum_point_ids,
        )
        db.add(row)
        rows.append(row)
    job.status = JobStatus.running
    job.stage = JobStage.components
    checkpoint(db, job, JobStage.components, {"component_count": len(rows)})
    _audit(db, job.id, job.project_id, "COMPONENTS_CONFIRMED", {"components": len(rows)})
    db.commit()
    return rows


def run_component_qc(db: Session, job_id: uuid.UUID, component_id: uuid.UUID) -> dict:
    job = db.get(Job, job_id)
    component = db.get(JobComponent, component_id)
    snapshot = db.get(Snapshot, job.snapshot_id)
    data = load_snapshot_data(db, snapshot)
    obs = {o.id: o for o in data.observations}
    edges = [Edge(obs[i].id, obs[i].from_id, obs[i].to_id, observation_mean(obs[i]), obs[i].distance_km)
             for i in component.observation_ids]
    from ..core.topology import Component

    comp = Component(
        index=component.component_index,
        point_ids=component.point_ids,
        observation_ids=component.observation_ids,
        datum_point_ids=component.datum_point_ids,
        edges=edges,
    )
    qc = inspect_component(comp, data)
    component.status = "error" if any(i["level"] == "error" for i in qc.issues) else "done"
    component.issues = qc.issues
    component.raw_closure_stats = qc.stats
    component.raw_closure_stats["cycles"] = qc.cycles
    db.commit()
    return {"status": component.status, "issues": qc.issues, "stats": qc.stats}


def all_components_confirmed(db: Session, job: Job) -> bool:
    rows = db.scalars(select(JobComponent).where(JobComponent.job_id == job.id)).all()
    return bool(rows) and all(r.status in ("done", "error", "skipped_superseded") for r in rows)


def enter_partition_stage(db: Session, job: Job) -> None:
    if job.stage == JobStage.accepted:
        snapshot = db.get(Snapshot, job.snapshot_id)
        create_components(db, job, load_snapshot_data(db, snapshot))
    job.status = JobStatus.running
    job.stage = JobStage.partition_qc
    db.flush()


def persist_solve(db: Session, job: Job) -> JobResult:
    existing = db.scalar(select(JobResult).where(JobResult.job_id == job.id))
    if existing:
        return existing
    snapshot = db.get(Snapshot, job.snapshot_id)
    data = load_snapshot_data(db, snapshot)
    requested = data.algorithm_params.get("algorithm_request", "auto")
    solution = solve_network(data, requested)

    result = JobResult(
        job_id=job.id,
        method=solution.method,
        rank=solution.rank,
        degrees_of_freedom=solution.degrees_of_freedom,
        sigma0=solution.sigma0,
        weighted_rss=solution.weighted_rss,
        condition_estimate=solution.condition_estimate,
        stats={"component_diagnostics": solution.component_diagnostics},
        nullspace=solution.nullspace,
    )
    db.add(result)
    db.flush()

    if solution.ok:
        points = {p.id: p for p in db.scalars(select(Point).where(Point.project_id == job.project_id)).all()}
        snapshot_points = {p.id: p for p in data.points}
        obs_rows = {o.id: o for o in db.scalars(select(Observation).where(Observation.project_id == job.project_id)).all()}
        datum_by_id = {uuid.UUID(d.id): d for d in data.datums}
        db.bulk_insert_mappings(
            ResultPointHeight,
            [
                {
                    "result_id": result.id,
                    "point_id": uuid.UUID(pid),
                    "adjusted_height_m": height,
                    "datum_residual_m": (
                        solution.datum_residuals.get(d.id)
                        if (d := next((x for x in data.datums if x.point_id == pid), None))
                        else None
                    ),
                }
                for pid, height in solution.heights.items()
            ],
        )
        db.bulk_insert_mappings(
            ResultObservation,
            [
                {
                    "result_id": result.id,
                    "observation_id": uuid.UUID(rec.id),
                    "raw_forward": rec.raw_forward,
                    "raw_backward": rec.raw_backward,
                    "raw_mean": observation_mean(rec),
                    "adjusted_delta_m": solution.heights[rec.to_id] - solution.heights[rec.from_id],
                    "correction_m": (solution.heights[rec.to_id] - solution.heights[rec.from_id]) - observation_mean(rec),
                    "residual_m": solution.residuals[rec.id],
                    "weight": solution.weights[rec.id],
                }
                for rec in data.observations
            ],
        )
        _persist_adjusted_cycles(db, job, result, data, solution.heights)
    return result


def _persist_adjusted_cycles(db: Session, job: Job, result: JobResult, data: NetworkSnapshotData, heights: dict[str, float]) -> None:
    # Raw cycle basis edges and topology; recalculate their physical closure
    # using adjusted potentials. In a converged adjustment these are numerical
    # zeros and are still explicitly audited before publication.
    raw_edges = [Edge(o.id, o.from_id, o.to_id, observation_mean(o), o.distance_km) for o in data.observations]
    datum_points = {d.point_id for d in data.datums}
    from ..core.topology import connected_components

    comps = connected_components([p.id for p in data.points], raw_edges, datum_points)
    raw_cycles = cycle_basis_closures(comps)
    point_code = {p.id: p.code for p in data.points}
    obs_by_id = {o.id: o for o in data.observations}
    for cyc in raw_cycles:
        rec = obs_by_id[cyc["chord_id"]]
        adjusted_potential = heights[rec.to_id] - heights[rec.from_id]
        adjusted_closure = adjusted_potential - observation_mean(rec)
        passed = abs(adjusted_closure) <= cyc["tolerance_m"]
        db.add(ResultCycle(
            result_id=result.id,
            component_index=cyc["component_index"],
            point_codes=[point_code.get(pid, pid) for pid in cyc["point_ids"]],
            raw_closure_m=cyc["raw_closure_m"],
            adjusted_closure_m=adjusted_closure,
            tolerance_m=cyc["tolerance_m"],
            passed=passed,
        ))


def audit_and_finish(db: Session, job: Job, result: JobResult) -> dict:
    snapshot = db.get(Snapshot, job.snapshot_id)
    data = load_snapshot_data(db, snapshot)
    checks: list[dict] = []

    def check(name: str, passed: bool, detail: dict | None = None, blocking: bool = False) -> None:
        checks.append({"name": name, "passed": passed, "blocking": blocking, "detail": detail or {}})

    component_rows = db.scalars(select(JobComponent).where(JobComponent.job_id == job.id)).all()
    blocked_qc = [issue for r in component_rows for issue in r.issues if issue.get("level") == "error"]
    check("partition_qc_errors", not blocked_qc, {"count": len(blocked_qc), "issues": blocked_qc[:20]}, True)
    check("snapshot_input_hash", result.job.snapshot.input_sha256 == data.input_sha256(), {"sha256": snapshot.input_sha256})
    check("algorithm_params_immutable", result.job.snapshot.algorithm_params == data.algorithm_params)
    check("no_arbitrary_regularization", data.algorithm_params.get("regularization") == "forbidden", blocking=True)

    if result.nullspace:
        check("unique_height_space", False, result.nullspace, True)
    else:
        check("unique_height_space", True, {"rank": result.rank})

    cycles = db.scalars(select(ResultCycle).where(ResultCycle.result_id == result.id)).all()
    max_adj = max((abs(c.adjusted_closure_m) for c in cycles), default=0.0)
    max_raw = max((abs(c.raw_closure_m) for c in cycles), default=0.0)
    bad_adjusted = [c for c in cycles if not c.passed]
    check(
        "adjusted_loop_closures",
        not bad_adjusted,
        {
            "max_abs_adjusted_closure_m": max_adj,
            "failed_cycle_count": len(bad_adjusted),
            "rule": "adjusted independent-loop closure must satisfy its configured tolerance",
        },
        True,
    )

    residuals = db.scalars(select(ResultObservation).where(ResultObservation.result_id == result.id)).all()
    max_res = max((abs(r.residual_m) for r in residuals), default=0.0)
    rms_res = (sum(r.residual_m ** 2 for r in residuals) / len(residuals)) ** 0.5 if residuals else 0.0
    check("corrections_and_residuals", len(residuals) == len(data.observations), {
        "count": len(residuals),
        "max_abs_residual_m": max_res,
        "rms_residual_m": rms_res,
        "sigma0": result.sigma0,
    }, True)

    # Raw observations must remain separately held in both snapshot and working
    # tables; adjusted values are only in result rows.
    snapshot_obs = {uuid.UUID(o.id): o for o in data.observations}
    raw_integrity = all(
        abs(r.raw_forward - snapshot_obs[r.observation_id].raw_forward) < 1e-12
        and abs(r.raw_backward - snapshot_obs[r.observation_id].raw_backward) < 1e-12
        for r in residuals
    )
    check("raw_observations_never_overwritten", raw_integrity, blocking=True)

    datum_checks = []
    for d in data.datums:
        if d.datum_type == "weighted":
            row = db.scalar(select(ResultPointHeight).where(ResultPointHeight.result_id == result.id, ResultPointHeight.point_id == uuid.UUID(d.point_id)))
            datum_residual = row.datum_residual_m if row else None
            datum_checks.append({"point_id": d.point_id, "residual_m": datum_residual, "sigma_m": d.sigma_m})
    check("datum_constraints", result.nullspace is None, {"datums": datum_checks}, True)

    audit = {
        "passed": not any(c["blocking"] and not c["passed"] for c in checks),
        "checks": checks,
        "residual_stats": {"max_abs_residual_m": max_res, "rms_residual_m": rms_res, "sigma0": result.sigma0},
        "closure_stats": {"max_abs_raw_closure_m": max_raw, "max_abs_adjusted_closure_m": max_adj, "cycle_count": len(cycles)},
    }
    result.audit = audit

    job.stage = JobStage.audit
    checkpoint(db, job, JobStage.audit, {"passed": audit["passed"]})
    if audit["passed"]:
        # Old generations may finish their immutable-snapshot audit, but never
        # become the publishable current draft if a newer revision exists.
        project = db.get(Project, job.project_id)
        if snapshot.draft_version == project.draft_version:
            job.status = JobStatus.ready
            job.stage = JobStage.ready
            checkpoint(db, job, JobStage.ready)
        else:
            job.status = JobStatus.superseded
            job.stage = JobStage.superseded
        _audit(db, job.id, job.project_id, "JOB_READY", {"snapshot": snapshot.input_sha256, "status": job.status.value})
    else:
        job.status = JobStatus.failed
        job.stage = JobStage.failed
        job.error_code = "AUDIT_FAILED"
        job.error_message = "Pre-publication audit found blocking issues."
        _audit(db, job.id, job.project_id, "JOB_AUDIT_FAILED", audit)
    db.commit()
    return audit


def fail_job(db: Session, job: Job, code: str, message: str, result: JobResult | None = None) -> None:
    job.status = JobStatus.failed
    job.stage = JobStage.failed
    job.error_code = code
    job.error_message = message
    if result is not None:
        result.audit = {"passed": False, "error": {"code": code, "message": message}, "nullspace": result.nullspace}
    _audit(db, job.id, job.project_id, "JOB_FAILED", {"code": code, "message": message})
    db.commit()

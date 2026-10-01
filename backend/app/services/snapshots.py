from __future__ import annotations

import csv
import io
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    DatumRule,
    DatumType,
    Job,
    JobStage,
    JobStatus,
    Observation,
    Point,
    Project,
    Snapshot,
    WeightRule,
)
from ..core.snapshot import (
    DatumRecord,
    NetworkSnapshotData,
    ObservationRecord,
    PointRecord,
    WeightRules,
)
from ..core.weights import observation_mean


def get_or_create_project(db: Session, code: str, name: str) -> Project:
    project = db.scalar(select(Project).where(Project.code == code))
    if project:
        return project
    project = Project(code=code, name=name)
    db.add(project)
    db.flush()
    db.add(WeightRule(project_id=project.id))
    db.flush()
    return project


def parse_csv(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    required = {"from_code", "to_code", "raw_forward", "raw_backward", "distance_km"}
    if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
        raise ValueError(f"CSV must contain columns: {sorted(required)}")
    for row in reader:
        rows.append(
            {
                "line_code": row.get("line_code") or f"L{len(rows) + 1}",
                "sequence": int(row.get("sequence") or 1),
                "from_code": row["from_code"],
                "to_code": row["to_code"],
                "raw_forward": float(row["raw_forward"]),
                "raw_backward": float(row["raw_backward"]),
                "distance_km": float(row["distance_km"]),
                "sigma_add_mm": float(row["sigma_add_mm"]) if row.get("sigma_add_mm") else None,
                "sigma_per_km_mm": float(row["sigma_per_km_mm"]) if row.get("sigma_per_km_mm") else None,
                "x": float(row["x"]) if row.get("x") else None,
                "y": float(row["y"]) if row.get("y") else None,
            }
        )
    return rows


def import_rows(db: Session, project: Project, rows: list[dict]) -> dict:
    points = {p.code: p for p in db.scalars(select(Point).where(Point.project_id == project.id)).all()}
    created_points = 0
    created_observations = 0

    new_points: list[Point] = []
    new_observations: list[Observation] = []

    for row in rows:
        for code in (row["from_code"], row["to_code"]):
            if code not in points:
                wkt = f"SRID=4326;POINT({row['x']} {row['y']})" if row.get("x") is not None and row.get("y") is not None else None
                point = Point(project_id=project.id, code=code, name=code, geom_wkt=wkt, geom_geometry=wkt)
                new_points.append(point)
                points[code] = point
                created_points += 1

    # Bulk insert points first; SQLAlchemy UUID defaults are populated before
    # observation foreign keys are materialized.
    for point in new_points:
        if point.id is None:
            point.id = uuid.uuid4()
    if new_points:
        db.bulk_save_objects(new_points)
        db.flush()

    for row in rows:
        observation = Observation(
            id=uuid.uuid4(),
            project_id=project.id,
            line_code=row["line_code"],
            sequence=row["sequence"],
            from_point_id=points[row["from_code"]].id,
            to_point_id=points[row["to_code"]].id,
            raw_forward=row["raw_forward"],
            raw_backward=row["raw_backward"],
            distance_km=row["distance_km"],
            sigma_add_mm=row.get("sigma_add_mm"),
            sigma_per_km_mm=row.get("sigma_per_km_mm"),
        )
        new_observations.append(observation)
        created_observations += 1

    db.bulk_save_objects(new_observations)
    from .supersession import supersede_project_jobs

    project.draft_version += 1
    supersede_project_jobs(db, project, "observations imported")
    db.flush()
    return {"created_points": created_points, "created_observations": created_observations, "draft_version": project.draft_version}


def add_datum(db: Session, project: Project, point_code: str, datum_type: str, elevation: float, sigma: float | None) -> DatumRule:
    point = db.scalar(select(Point).where(Point.project_id == project.id, Point.code == point_code))
    if not point:
        raise ValueError("unknown point code")
    existing = db.scalar(select(DatumRule).where(DatumRule.project_id == project.id, DatumRule.point_id == point.id))
    if existing:
        raise ValueError("datum rule already exists; revise it with optimistic lock")
    if datum_type == "weighted" and not sigma:
        raise ValueError("weighted datum requires sigma_m")
    datum = DatumRule(
        project_id=project.id,
        point_id=point.id,
        datum_type=DatumType[datum_type],
        elevation_m=elevation,
        sigma_m=sigma,
    )
    db.add(datum)
    from .supersession import supersede_project_jobs

    project.draft_version += 1
    supersede_project_jobs(db, project, "datum added")
    db.flush()
    return datum


def build_snapshot_data(db: Session, project: Project, algorithm: str = "auto") -> NetworkSnapshotData:
    points = db.scalars(select(Point).where(Point.project_id == project.id).order_by(Point.code)).all()
    observations = db.scalars(
        select(Observation)
        .where(Observation.project_id == project.id, Observation.is_active.is_(True))
        .order_by(Observation.line_code, Observation.sequence)
    ).all()
    datums = db.scalars(
        select(DatumRule).where(DatumRule.project_id == project.id, DatumRule.is_active.is_(True)).order_by(DatumRule.point_id)
    ).all()
    weights = db.scalar(select(WeightRule).where(WeightRule.project_id == project.id))
    if weights is None:
        weights = WeightRule(project_id=project.id)
        db.add(weights)
        db.flush()

    def xy(point: Point):
        if not point.geom_wkt or "POINT(" not in point.geom_wkt:
            return None, None
        body = point.geom_wkt.split("POINT(", 1)[1].rstrip(")")
        x, y = body.split()
        return float(x), float(y)

    point_records = [PointRecord(str(p.id), p.code, *xy(p)) for p in points]
    obs_records = [
        ObservationRecord(
            id=str(o.id),
            stable_key=f"{o.line_code}#{o.sequence}",
            line_code=o.line_code,
            sequence=o.sequence,
            from_id=str(o.from_point_id),
            to_id=str(o.to_point_id),
            raw_forward=o.raw_forward,
            raw_backward=o.raw_backward,
            distance_km=o.distance_km,
            sigma_add_mm=o.sigma_add_mm,
            sigma_per_km_mm=o.sigma_per_km_mm,
        )
        for o in observations
    ]
    datum_records = [
        DatumRecord(str(d.id), str(d.point_id), d.datum_type.value, d.elevation_m, d.sigma_m) for d in datums
    ]
    rules = WeightRules(
        sigma_add_mm=weights.sigma_add_mm,
        sigma_per_km_mm=weights.sigma_per_km_mm,
        forward_backward_factor=weights.forward_backward_factor,
        reject_min_distance_km=weights.reject_min_distance_km,
        custom_weights={str(k): v for k, v in weights.custom_weights.items()},
    )
    settings = get_settings()
    params = {
        "algorithm_request": algorithm,
        "dense_qr_limit": settings.dense_qr_limit,
        "condition_warning": settings.condition_warning,
        "condition_failure": settings.condition_failure,
        "svd_rank_eps": settings.svd_rank_eps,
        "qr_rank_tol": settings.qr_rank_tol,
        "weight_model": "inverse_sigma_squared",
        "height_difference": "0.5*(forward - backward)",
        "scipy_linalg": "sparse_LU_normal_equations_then_explicit_QR_diagnostic",
        "regularization": "forbidden",
    }
    return NetworkSnapshotData(
        project_code=project.code,
        points=point_records,
        observations=obs_records,
        datums=datum_records,
        weight_rules=rules,
        algorithm_params=params,
        point_code_by_id={str(p.id): p.code for p in points},
    )


def create_or_get_snapshot(db: Session, project: Project, data: NetworkSnapshotData) -> tuple[Snapshot, bool]:
    input_hash = data.input_sha256()
    existing = db.scalar(
        select(Snapshot).where(Snapshot.project_id == project.id, Snapshot.input_sha256 == input_hash)
    )
    if existing:
        return existing, False
    obs_h, datum_h, weights_h = data.partial_hashes()
    snapshot = Snapshot(
        project_id=project.id,
        draft_version=project.draft_version,
        input_sha256=input_hash,
        observations_sha256=obs_h,
        datums_sha256=datum_h,
        weights_sha256=weights_h,
        canonical_input=data.canonical_json(),
        algorithm_params=data.algorithm_params,
        point_count=len(data.points),
        observation_count=len(data.observations),
    )
    db.add(snapshot)
    db.flush()
    return snapshot, True


def submit_job(db: Session, project: Project, expected_version: int, algorithm: str = "auto") -> tuple[Job, bool]:
    if project.draft_version != expected_version:
        raise ValueError(f"draft_version conflict: expected {expected_version}, current {project.draft_version}")
    data = build_snapshot_data(db, project, algorithm)
    snapshot, _ = create_or_get_snapshot(db, project, data)

    # Same snapshot must never fan out into duplicate generations. It can be
    # reused while still retaining which project draft produced it.
    existing = db.scalar(
        select(Job)
        .where(Job.snapshot_id == snapshot.id)
        .order_by(Job.generation.desc())
    )
    if existing:
        return existing, False

    latest = db.scalar(select(Job).where(Job.project_id == project.id).order_by(Job.generation.desc()))
    generation = (latest.generation + 1 if latest else 1)
    job = Job(
        project_id=project.id,
        snapshot_id=snapshot.id,
        generation=generation,
        status=JobStatus.queued,
        stage=JobStage.accepted,
        confirmed_stage=JobStage.accepted,
    )
    db.add(job)
    db.flush()
    return job, True


def load_snapshot_data(db: Session, snapshot: Snapshot) -> NetworkSnapshotData:
    payload = json.loads(snapshot.canonical_input)
    internal = payload.get("internal", payload)
    stable = payload.get("stable", payload)
    data = NetworkSnapshotData(
        project_code=stable.get("project_code", ""),
        points=[PointRecord(**p) for p in internal["points"]],
        observations=[ObservationRecord(**o) for o in internal["observations"]],
        datums=[DatumRecord(**d) for d in internal["datums"]],
        weight_rules=WeightRules(**stable["weight_rules"]),
        algorithm_params=stable["algorithm_params"],
        point_code_by_id={p["id"]: p["code"] for p in internal["points"]},
    )
    return data


def observation_summary(record) -> dict:
    return {
        "raw_mean_m": observation_mean(record),
        "raw_forward_m": record.raw_forward,
        "raw_backward_m": record.raw_backward,
    }

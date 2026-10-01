from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import JSONBString, Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


class PostGISGeometry(TypeDecorator):
    """Production column is geometry(Point,4326); SQLite tests use text/no-op."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from geoalchemy2 import Geometry

            return dialect.type_descriptor(Geometry(geometry_type="POINT", srid=4326))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None or dialect.name != "postgresql":
            return value
        from geoalchemy2.elements import WKTElement

        return WKTElement(value, srid=4326) if isinstance(value, str) and value.startswith("SRID=") else value


class JobStage(str, enum.Enum):
    accepted = "accepted"
    components = "components"
    partition_qc = "partition_qc"
    global_solve = "global_solve"
    audit = "audit"
    ready = "ready"
    failed = "failed"
    superseded = "superseded"


class IssueLevel(str, enum.Enum):
    info = "info"
    warning = "warning"
    error = "error"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    # Optimistic lock for the mutable working draft. Immutable snapshots do not
    # carry a user-facing lock because they can never be edited.
    draft_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    points: Mapped[list[Point]] = relationship(back_populates="project", cascade="all, delete-orphan")
    observations: Mapped[list[Observation]] = relationship(back_populates="project", cascade="all, delete-orphan")
    datum_rules: Mapped[list[DatumRule]] = relationship(back_populates="project", cascade="all, delete-orphan")
    weight_rule: Mapped[WeightRule | None] = relationship(back_populates="project", uselist=False, cascade="all, delete-orphan")
    snapshots: Mapped[list[Snapshot]] = relationship(back_populates="project")


class Point(Base):
    __tablename__ = "points"
    __table_args__ = (UniqueConstraint("project_id", "code", name="uq_point_project_code"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    # SRID 4326 lon/lat. A mutable working copy only; snapshots retain x/y.
    geom_wkt: Mapped[str | None] = mapped_column(Text)
    geom_geometry: Mapped[object | None] = mapped_column(
        PostGISGeometry(),
        comment="PostGIS geometry(Point,4326); canonical x/y are also stored in snapshots",
    )

    project: Mapped[Project] = relationship(back_populates="points")


class Observation(Base):
    """Mutable working observation. raw_forward/raw_backward are never replaced
    by adjustments; adjusted values are written only to job result tables."""

    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("project_id", "line_code", "sequence", name="uq_observation_line_seq"),
        Index("ix_observation_project_from_to", "project_id", "from_point_id", "to_point_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    line_code: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    from_point_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("points.id", ondelete="RESTRICT"))
    to_point_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("points.id", ondelete="RESTRICT"))
    raw_forward: Mapped[float] = mapped_column(Float)
    raw_backward: Mapped[float] = mapped_column(Float)
    distance_km: Mapped[float] = mapped_column(Float)
    sigma_add_mm: Mapped[float | None] = mapped_column(Float)
    sigma_per_km_mm: Mapped[float | None] = mapped_column(Float)
    quality_flags: Mapped[dict] = mapped_column(JSONBString, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="observations", foreign_keys=[project_id])
    from_point: Mapped[Point] = relationship(foreign_keys=[from_point_id])
    to_point: Mapped[Point] = relationship(foreign_keys=[to_point_id])


class DatumType(str, enum.Enum):
    fixed = "fixed"
    weighted = "weighted"


class DatumRule(Base):
    __tablename__ = "datum_rules"
    __table_args__ = (UniqueConstraint("project_id", "point_id", name="uq_datum_project_point"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    point_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("points.id", ondelete="RESTRICT"))
    datum_type: Mapped[DatumType] = mapped_column(Enum(DatumType), default=DatumType.fixed)
    elevation_m: Mapped[float] = mapped_column(Float)
    sigma_m: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="datum_rules")
    point: Mapped[Point] = relationship()


class WeightRule(Base):
    __tablename__ = "weight_rules"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    # sigma_mm = sqrt(sigma_add_mm^2 + (sigma_per_km_mm^2 * distance_km)); weight = 1/sigma_metre^2
    sigma_add_mm: Mapped[float] = mapped_column(Float, default=1.0)
    sigma_per_km_mm: Mapped[float] = mapped_column(Float, default=2.0)
    forward_backward_factor: Mapped[float] = mapped_column(Float, default=1.0)
    reject_min_distance_km: Mapped[float | None] = mapped_column(Float)
    custom_weights: Mapped[dict] = mapped_column(JSONBString, default=dict, comment="observation id override")
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="weight_rule")


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (
        Index("ix_snapshot_project_draft", "project_id", "draft_version"),
        UniqueConstraint("project_id", "input_sha256", name="uq_snapshot_project_input"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    draft_version: Mapped[int] = mapped_column(Integer)
    input_sha256: Mapped[str] = mapped_column(String(64), index=True)
    observations_sha256: Mapped[str] = mapped_column(String(64))
    datums_sha256: Mapped[str] = mapped_column(String(64))
    weights_sha256: Mapped[str] = mapped_column(String(64))
    canonical_input: Mapped[str] = mapped_column(Text)
    algorithm_params: Mapped[dict] = mapped_column(JSONBString)
    point_count: Mapped[int] = mapped_column(Integer)
    observation_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="snapshots")


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    ready = "ready"
    failed = "failed"
    superseded = "superseded"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "generation", name="uq_job_snapshot_generation"),
        Index("ix_job_project_status_stage", "project_id", "status", "stage"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("snapshots.id", ondelete="RESTRICT"))
    generation: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued, index=True)
    stage: Mapped[JobStage] = mapped_column(Enum(JobStage), default=JobStage.accepted)
    # Last stage whose outputs are durable and confirmed. Worker restarts resume
    # after this point rather than repeating confirmed work.
    confirmed_stage: Mapped[JobStage] = mapped_column(Enum(JobStage), default=JobStage.accepted)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship()
    snapshot: Mapped[Snapshot] = relationship()
    checkpoints: Mapped[list[JobCheckpoint]] = relationship(back_populates="job", cascade="all, delete-orphan")
    result: Mapped[JobResult | None] = relationship(back_populates="job", uselist=False, cascade="all, delete-orphan")


class JobCheckpoint(Base):
    __tablename__ = "job_checkpoints"
    __table_args__ = (UniqueConstraint("job_id", "stage", name="uq_checkpoint_job_stage"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[JobStage] = mapped_column(Enum(JobStage))
    state: Mapped[dict] = mapped_column(JSONBString, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="checkpoints")


class JobComponent(Base):
    """Partition work item. Component results are QC evidence only; the solve
    stage always reconstructs a global coefficient matrix from the snapshot."""

    __tablename__ = "job_components"
    __table_args__ = (UniqueConstraint("job_id", "component_index", name="uq_component_job_index"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    component_index: Mapped[int] = mapped_column(Integer)
    point_ids: Mapped[list] = mapped_column(JSONBString)
    observation_ids: Mapped[list] = mapped_column(JSONBString)
    datum_point_ids: Mapped[list] = mapped_column(JSONBString, default=list)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    issues: Mapped[list] = mapped_column(JSONBString, default=list)
    raw_closure_stats: Mapped[dict] = mapped_column(JSONBString, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    job: Mapped[Job] = relationship()


class JobResult(Base):
    __tablename__ = "job_results"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    method: Mapped[str] = mapped_column(String(32))  # sparse_normal_equations | qr_diagnostic
    rank: Mapped[int] = mapped_column(Integer)
    degrees_of_freedom: Mapped[int] = mapped_column(Integer)
    sigma0: Mapped[float | None] = mapped_column(Float)
    weighted_rss: Mapped[float | None] = mapped_column(Float)
    condition_estimate: Mapped[float | None] = mapped_column(Float)
    stats: Mapped[dict] = mapped_column(JSONBString, default=dict)
    nullspace: Mapped[dict | None] = mapped_column(JSONBString)
    audit: Mapped[dict] = mapped_column(JSONBString, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="result")
    point_heights: Mapped[list[ResultPointHeight]] = relationship(cascade="all, delete-orphan")
    observation_residuals: Mapped[list[ResultObservation]] = relationship(cascade="all, delete-orphan")
    cycles: Mapped[list[ResultCycle]] = relationship(cascade="all, delete-orphan")


class ResultPointHeight(Base):
    __tablename__ = "result_point_heights"
    __table_args__ = (Index("ix_result_height_result_point", "result_id", "point_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_results.id", ondelete="CASCADE"), index=True)
    point_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("points.id", ondelete="RESTRICT"))
    adjusted_height_m: Mapped[float] = mapped_column(Float)
    datum_residual_m: Mapped[float | None] = mapped_column(Float)

    point: Mapped[Point] = relationship()


class ResultObservation(Base):
    __tablename__ = "result_observations"
    __table_args__ = (Index("ix_result_obs_result_obs", "result_id", "observation_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_results.id", ondelete="CASCADE"), index=True)
    observation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("observations.id", ondelete="RESTRICT"))
    raw_forward: Mapped[float] = mapped_column(Float)
    raw_backward: Mapped[float] = mapped_column(Float)
    raw_mean: Mapped[float] = mapped_column(Float)
    adjusted_delta_m: Mapped[float] = mapped_column(Float)
    correction_m: Mapped[float] = mapped_column(Float)
    residual_m: Mapped[float] = mapped_column(Float)
    weight: Mapped[float] = mapped_column(Float)

    observation: Mapped[Observation] = relationship()


class ResultCycle(Base):
    __tablename__ = "result_cycles"

    id: Mapped[uuid.UUID] = uuid_pk()
    result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_results.id", ondelete="CASCADE"), index=True)
    component_index: Mapped[int | None] = mapped_column(Integer)
    point_codes: Mapped[list] = mapped_column(JSONBString)
    raw_closure_m: Mapped[float] = mapped_column(Float)
    adjusted_closure_m: Mapped[float] = mapped_column(Float)
    tolerance_m: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Boolean)


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("project_id", "published_version", name="uq_publication_project_version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"))
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("snapshots.id", ondelete="RESTRICT"))
    published_version: Mapped[int] = mapped_column(Integer)
    input_sha256: Mapped[str] = mapped_column(String(64), index=True)
    algorithm_params: Mapped[dict] = mapped_column(JSONBString)
    summary: Mapped[dict] = mapped_column(JSONBString)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship()
    snapshot: Mapped[Snapshot] = relationship()


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(128), default="api")
    detail: Mapped[dict] = mapped_column(JSONBString, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

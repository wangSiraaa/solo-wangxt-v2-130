from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectCreate(BaseModel):
    code: str
    name: str


class ProjectOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    draft_version: int
    created_at: datetime
    updated_at: datetime


class PointIn(BaseModel):
    code: str
    name: str | None = None
    x: float | None = Field(None, description="longitude")
    y: float | None = Field(None, description="latitude")


class PointOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str | None
    geom_wkt: str | None


class ObservationIn(BaseModel):
    line_code: str
    sequence: int = 1
    from_code: str
    to_code: str
    raw_forward: float
    raw_backward: float
    distance_km: float = Field(gt=0)
    sigma_add_mm: float | None = None
    sigma_per_km_mm: float | None = None


class ObservationRevision(BaseModel):
    raw_forward: float | None = None
    raw_backward: float | None = None
    distance_km: float | None = Field(None, gt=0)
    is_active: bool | None = None
    expected_version: int


class ObservationOut(ORMModel):
    id: uuid.UUID
    line_code: str
    sequence: int
    from_point_id: uuid.UUID
    to_point_id: uuid.UUID
    raw_forward: float
    raw_backward: float
    distance_km: float
    is_active: bool
    lock_version: int


class DatumIn(BaseModel):
    point_code: str
    datum_type: Literal["fixed", "weighted"] = "fixed"
    elevation_m: float
    sigma_m: float | None = Field(None, gt=0)


class DatumRevision(BaseModel):
    elevation_m: float | None = None
    sigma_m: float | None = Field(None, gt=0)
    datum_type: Literal["fixed", "weighted"] | None = None
    is_active: bool | None = None
    expected_version: int


class DatumOut(ORMModel):
    id: uuid.UUID
    point_id: uuid.UUID
    datum_type: str
    elevation_m: float
    sigma_m: float | None
    is_active: bool
    lock_version: int


class WeightRuleIn(BaseModel):
    sigma_add_mm: float = Field(default=1.0, ge=0)
    sigma_per_km_mm: float = Field(default=2.0, ge=0)
    forward_backward_factor: float = Field(default=1.0, gt=0)
    reject_min_distance_km: float | None = Field(None, gt=0)
    custom_weights: dict[str, float] = Field(default_factory=dict)
    expected_version: int


class WeightRuleOut(ORMModel):
    id: uuid.UUID
    sigma_add_mm: float
    sigma_per_km_mm: float
    forward_backward_factor: float
    reject_min_distance_km: float | None
    custom_weights: dict[str, float]
    lock_version: int


class ImportResult(BaseModel):
    created_points: int
    created_observations: int
    draft_version: int
    input_sha256: str | None = None


class JobSubmit(BaseModel):
    expected_draft_version: int
    algorithm: Literal["auto", "normal", "qr"] = "auto"


class JobOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    snapshot_id: uuid.UUID
    generation: int
    status: str
    stage: str
    confirmed_stage: str
    attempt: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    heartbeat_at: datetime | None


class Issue(BaseModel):
    level: Literal["info", "warning", "error"]
    code: str
    message: str
    component_index: int | None = None
    ids: list[str] = Field(default_factory=list)


class HeightOut(BaseModel):
    point_id: uuid.UUID
    point_code: str
    adjusted_height_m: float
    datum_residual_m: float | None = None


class ResidualOut(BaseModel):
    observation_id: uuid.UUID
    line_code: str
    raw_mean: float
    adjusted_delta_m: float
    correction_m: float
    residual_m: float
    weight: float


class ResultOut(BaseModel):
    job: JobOut
    method: str
    rank: int
    degrees_of_freedom: int
    sigma0: float | None
    weighted_rss: float | None
    condition_estimate: float | None
    stats: dict
    nullspace: dict | None
    audit: dict
    heights: list[HeightOut]
    residuals: list[ResidualOut]


class PublishRequest(BaseModel):
    expected_draft_version: int


class PublicationOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    job_id: uuid.UUID
    snapshot_id: uuid.UUID
    published_version: int
    input_sha256: str
    algorithm_params: dict
    summary: dict
    published_at: datetime


class TopologyOut(BaseModel):
    nodes: list[dict]
    edges: list[dict]
    components: list[dict]

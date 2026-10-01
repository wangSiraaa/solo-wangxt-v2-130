from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DatumRule, DatumType, Observation, Project, WeightRule
from .supersession import supersede_project_jobs


class ConflictError(RuntimeError):
    pass


def _bump_project(project: Project, reason: str, db: Session) -> None:
    project.draft_version += 1
    supersede_project_jobs(db, project, reason)


def revise_observation(db: Session, project: Project, obs: Observation, changes: dict) -> Observation:
    if obs.lock_version != changes.pop("expected_version"):
        raise ConflictError("observation lock_version mismatch")
    for key in ("raw_forward", "raw_backward", "distance_km", "is_active"):
        if changes.get(key) is not None:
            setattr(obs, key, changes[key])
    obs.lock_version += 1
    _bump_project(project, "draft revised", db)
    db.flush()
    return obs


def revise_datum(db: Session, project: Project, datum: DatumRule, changes: dict) -> DatumRule:
    expected = changes.pop("expected_version")
    if datum.lock_version != expected:
        raise ConflictError("datum lock_version mismatch")
    for key in ("elevation_m", "sigma_m", "datum_type", "is_active"):
        value = changes.get(key)
        if value is not None:
            setattr(datum, key, DatumType[value] if key == "datum_type" else value)
    if datum.datum_type.value == "weighted" and not datum.sigma_m:
        raise ValueError("weighted datum requires sigma_m")
    datum.lock_version += 1
    _bump_project(project, "draft revised", db)
    db.flush()
    return datum


def revise_weights(db: Session, project: Project, rule: WeightRule, changes: dict) -> WeightRule:
    expected = changes.pop("expected_version")
    if rule.lock_version != expected:
        raise ConflictError("weight rule lock_version mismatch")
    for key in ("sigma_add_mm", "sigma_per_km_mm", "forward_backward_factor", "reject_min_distance_km", "custom_weights"):
        if changes.get(key) is not None:
            setattr(rule, key, changes[key])
    rule.lock_version += 1
    _bump_project(project, "draft revised", db)
    db.flush()
    return rule

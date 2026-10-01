from __future__ import annotations

import math

from .snapshot import ObservationRecord, WeightRules


def observation_mean(record: ObservationRecord) -> float:
    """Mean height difference in metres.

    Forward and backward are independent levelling runs. Their discrepancy is
    quality evidence; both raw values remain retained separately.
    """

    return 0.5 * (record.raw_forward - record.raw_backward)


def observation_misclosure(record: ObservationRecord) -> float:
    """Signed forward+backward run misclosure in metres."""

    return record.raw_forward + record.raw_backward


def sigma_metres(record: ObservationRecord, rules: WeightRules) -> float:
    add_mm = record.sigma_add_mm if record.sigma_add_mm is not None else rules.sigma_add_mm
    per_km_mm = record.sigma_per_km_mm if record.sigma_per_km_mm is not None else rules.sigma_per_km_mm
    add_m = add_mm / 1000.0
    per_km_m = per_km_mm / 1000.0
    return math.sqrt(add_m * add_m + rules.forward_backward_factor * per_km_m * per_km_m * record.distance_km)


def weight_for(record: ObservationRecord, rules: WeightRules) -> float:
    override = rules.custom_weights.get(record.id) or rules.custom_weights.get(record.stable_key)
    if override is not None:
        if not math.isfinite(override) or override <= 0:
            raise ValueError(f"custom weight for observation {record.id} must be positive and finite")
        return float(override)
    sigma = sigma_metres(record, rules)
    if not math.isfinite(sigma) or sigma <= 0:
        raise ValueError(f"non-positive sigma for observation {record.id}")
    return 1.0 / (sigma * sigma)

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import MatrixRankWarning, splu, svds
from scipy.linalg import qr as dense_qr

from .snapshot import NetworkSnapshotData
from .topology import Component, Edge, connected_components
from .weights import observation_mean, weight_for


@dataclass
class SolveResult:
    ok: bool
    method: str
    heights: dict[str, float]
    residuals: dict[str, float]
    datum_residuals: dict[str, float]
    weights: dict[str, float]
    rank: int
    degrees_of_freedom: int
    sigma0: float | None
    weighted_rss: float | None
    condition_estimate: float | None
    component_diagnostics: list[dict[str, Any]]
    nullspace: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


def solve_network(snapshot: NetworkSnapshotData, requested_method: str = "auto") -> SolveResult:
    """Weighted least squares from one immutable snapshot.

    No ridge term, pseudo-inverse minimum-norm trick, or arbitrary datum shift
    is used. Exact fixed points are eliminated; weighted datums become ordinary
    observation rows. If the remaining design matrix is rank deficient the job
    fails and returns a nullspace diagnostic instead of unique published heights.
    """

    points = sorted(snapshot.points, key=lambda p: p.id)
    observations = sorted(snapshot.observations, key=lambda o: o.id)
    point_index = {p.id: i for i, p in enumerate(points)}
    fixed = {d.point_id: d.elevation_m for d in snapshot.datums if d.datum_type == "fixed"}
    weighted = [d for d in snapshot.datums if d.datum_type == "weighted"]
    weighted_map = {d.point_id: d for d in weighted}

    edges: list[Edge] = []
    means: dict[str, float] = {}
    weights: dict[str, float] = {}
    for rec in observations:
        value = observation_mean(rec)
        w = weight_for(rec, snapshot.weight_rules)
        means[rec.id] = value
        weights[rec.id] = w
        edges.append(Edge(rec.id, rec.from_id, rec.to_id, value, rec.distance_km))

    components = connected_components([p.id for p in points], edges, {d.point_id for d in snapshot.datums})
    diagnostics = []
    bad_component = False
    ill_component = False
    cond_values: list[float] = []

    params = snapshot.algorithm_params
    dense_limit = int(params.get("dense_qr_limit", 5_000))
    cond_warn = float(params.get("condition_warning", 1e10))
    cond_fail = float(params.get("condition_failure", 1e14))
    rank_eps = float(params.get("svd_rank_eps", 1e-10))
    qr_tol = float(params.get("qr_rank_tol", 1e-10))

    free_set = {p.id for p in points if p.id not in fixed}
    free_ids = sorted(free_set)
    free_idx = {pid: i for i, pid in enumerate(free_ids)}

    for comp in components:
        point_set = set(comp.point_ids)
        weighted_in_comp = [d for d in weighted if d.point_id in point_set]
        unknown_count = len([pid for pid in comp.point_ids if pid not in fixed])
        info: dict[str, Any] = {
            "component_index": comp.index,
            "point_count": len(comp.point_ids),
            "unknown_count": unknown_count,
            "equation_count": len(comp.edges) + len(weighted_in_comp),
            "fixed_count": len(comp.datum_point_ids) - len(weighted_in_comp),
            "weighted_datum_count": len(weighted_in_comp),
            "condition_estimate": None,
            "svd_available": False,
            "rank_deficient": False,
        }
        if unknown_count == 0:
            info.update(method="constant", rank=0, condition_estimate=1.0)
        elif not comp.datum_point_ids:
            info.update(rank_deficient=True, error_code="UNCONSTRAINED_COMPONENT")
            info["nullspace"] = {"dimension": unknown_count, "point_ids": [comp.point_ids[0]], "message": "free vertical shift"}
            bad_component = True
        diagnostics.append(info)

    if bad_component and requested_method != "qr":
        null = _first_nullspace(diagnostics)
        return SolveResult(
            ok=False,
            method="qr_diagnostic" if ill_component else "sparse_normal_equations",
            heights={},
            residuals={},
            datum_residuals={},
            weights=weights,
            rank=sum(d.get("matrix_rank", 0) for d in diagnostics),
            degrees_of_freedom=0,
            sigma0=None,
            weighted_rss=None,
            condition_estimate=max(cond_values, default=None),
            component_diagnostics=diagnostics,
            nullspace=null,
            error_code=null.get("error_code", "RANK_DEFICIENT") if null else "RANK_DEFICIENT",
            error_message=(null or {}).get("message", "Network does not define unique heights; no regularization applied."),
        )

    method = "qr_diagnostic" if requested_method == "qr" else "sparse_normal_equations"
    x = np.zeros(len(free_ids), dtype=float)
    lu = None
    if method == "sparse_normal_equations" and requested_method != "qr":
        try:
            x, rank, lu = _normal_equation_solve(free_ids, components, fixed, weighted_map, means, weights)
            condition = _lu_diagonal_condition(lu)
            for info in diagnostics:
                if info.get("unknown_count", 0) > 0:
                    info["condition_estimate"] = condition
                    info["normal_equation_condition_estimate"] = condition
            cond_values.append(condition)
            if condition >= cond_fail:
                return SolveResult(
                    False,
                    method,
                    {}, {}, {}, weights, rank, 0, None, None, condition, diagnostics,
                    {"error_code": "ILL_CONDITIONED_NORMAL_EQUATIONS", "condition_estimate": condition,
                     "message": "Normal equations exceed failure condition threshold; rerun explicit QR diagnostic."},
                    "ILL_CONDITIONED_NORMAL_EQUATIONS",
                    "Normal equations too ill-conditioned for unique publication; arbitrary regularization forbidden.",
                )
            if condition >= cond_warn:
                method = "qr_diagnostic"
        except (ValueError, MatrixRankWarning, RuntimeError) as exc:
            if requested_method == "normal":
                return SolveResult(False, "sparse_normal_equations", {}, {}, {}, weights, 0, 0, None, None, None,
                                   diagnostics, None, "NORMAL_EQUATION_SINGULAR", str(exc))
            method = "qr_diagnostic"

    if method == "qr_diagnostic":
        global_rank = 0
        for comp in components:
            diag = _component_matrix(comp, free_idx, fixed, weighted_map, means, weights)
            A, b = diag["A"], diag["b"]
            n = A.shape[1]
            if n == 0:
                continue
            xc, rank, null_info, ok = _qr_solve(A, b, free_ids, diag["free_ids"], qr_tol, dense_limit)
            comp_info = next(d for d in diagnostics if d["component_index"] == comp.index)
            comp_info["qr_rank"] = rank
            global_rank += rank
            if not ok:
                comp_info["nullspace"] = null_info
                return SolveResult(
                    False,
                    method,
                    {}, {}, {}, weights, rank, 0, None, None,
                    max(cond_values, default=None), diagnostics, null_info,
                    "RANK_DEFICIENT",
                    "QR found rank deficiency; unique solution refused and no regularization was added.",
                )
            x[np.ix_(diag["free_global"])] = xc
        rank = global_rank

    heights = dict(fixed)
    for pid, value in zip(free_ids, x):
        heights[pid] = float(value)

    obs_residuals: dict[str, float] = {}
    adjusted_delta: dict[str, float] = {}
    for rec in observations:
        adjusted = heights[rec.to_id] - heights[rec.from_id]
        adjusted_delta[rec.id] = adjusted
        obs_residuals[rec.id] = means[rec.id] - adjusted

    datum_residuals = {d.id: 0.0 for d in snapshot.datums if d.datum_type == "fixed"}
    for d in weighted:
        datum_residuals[d.id] = d.elevation_m - heights[d.point_id]

    rss_terms = [weights[r.id] * obs_residuals[r.id] ** 2 for r in observations]
    for d in weighted:
        if d.sigma_m and d.sigma_m > 0:
            rss_terms.append((datum_residuals[d.id] / d.sigma_m) ** 2)
    weighted_rss = float(sum(rss_terms))
    equation_count = len(observations) + len(weighted)
    dof = equation_count - rank
    sigma0 = math.sqrt(weighted_rss / dof) if dof > 0 else None

    return SolveResult(
        ok=True,
        method=method,
        heights={pid: float(v) for pid, v in heights.items()},
        residuals=obs_residuals,
        datum_residuals=datum_residuals,
        weights=weights,
        rank=rank,
        degrees_of_freedom=dof,
        sigma0=sigma0,
        weighted_rss=weighted_rss,
        condition_estimate=max(cond_values, default=None),
        component_diagnostics=diagnostics,
    )


def _component_matrix(comp, free_idx, fixed, weighted_map, means, weights):
    rows, cols, data, b = [], [], [], []
    free_global: list[int] = []
    local_seen: dict[str, int] = {}

    def local_col(pid: str) -> int:
        if pid not in local_seen:
            local_seen[pid] = len(local_seen)
            free_global.append(free_idx[pid])
        return local_seen[pid]

    row = 0
    sw = []
    for edge in comp.edges:
        rhs = means[edge.id]
        if edge.from_id in fixed:
            rhs += fixed[edge.from_id]
        else:
            rows.append(row); cols.append(local_col(edge.from_id)); data.append(-1.0)
        if edge.to_id in fixed:
            rhs -= fixed[edge.to_id]
        else:
            rows.append(row); cols.append(local_col(edge.to_id)); data.append(1.0)
        b.append(rhs)
        sw.append(math.sqrt(weights[edge.id]))
        row += 1

    point_set = set(comp.point_ids)
    for pid in sorted(point_set):
        datum = weighted_map.get(pid)
        if not datum:
            continue
        if pid in fixed:
            continue
        if not datum.sigma_m or datum.sigma_m <= 0:
            raise ValueError(f"weighted datum {datum.id} requires positive sigma_m")
        rows.append(row); cols.append(local_col(pid)); data.append(1.0)
        b.append(datum.elevation_m)
        sw.append(1.0 / datum.sigma_m)
        row += 1

    n = len(local_seen)
    A = sparse.csc_matrix((data, (rows, cols)), shape=(row, n))
    sqrtw = np.asarray(sw, dtype=float)
    A = A.multiply(sqrtw[:, None]).tocsr()
    bvec = np.asarray(b, dtype=float) * sqrtw
    return {"A": A, "b": bvec, "free_ids": list(local_seen.keys()), "free_global": free_global}


def _normal_equation_solve(free_ids, components, fixed, weighted_map, means, weights):
    n = len(free_ids)
    global_index = {pid: i for i, pid in enumerate(free_ids)}
    rows, cols, vals, rhs_rows, rhs_vals = [], [], [], [], []

    # Accumulate weighted observation rows directly as sparse outer products.
    # This avoids materializing/densifying a 100k x n normal matrix in Python.
    for comp in components:
        diag = _component_matrix(comp, global_index, fixed, weighted_map, means, weights)
        A, b, gidx = diag["A"], diag["b"], diag["free_global"]
        if not gidx:
            continue
        coo = A.tocoo()
        # A is tiny per row (one/two entries); construct AtA/Atb by bincount.
        lr, lc, lv = [], [], []
        for r, c, v in zip(coo.row, coo.col, coo.data):
            lr.append(int(r)); lc.append(int(c)); lv.append(float(v))
        A2 = sparse.csr_matrix((lv, (lr, lc)), shape=A.shape)
        ntn = (A2.T @ A2).tocoo()
        for i, j, v in zip(ntn.row, ntn.col, ntn.data):
            rows.append(gidx[i]); cols.append(gidx[j]); vals.append(float(v))
        local_rhs = A2.T @ b
        for i, v in enumerate(local_rhs):
            rhs_rows.append(gidx[i]); rhs_vals.append(float(v))

    N0 = sparse.coo_matrix((vals, (rows, cols)), shape=(n, n)).tocsr()
    N = N0.tocsc()
    rhs = np.zeros(n)
    np.add.at(rhs, np.asarray(rhs_rows), np.asarray(rhs_vals))
    try:
        lu = splu(N)
        x = lu.solve(rhs)
    except RuntimeError as exc:
        raise ValueError(f"sparse LU factorization failed: {exc}") from exc
    residual = float(np.linalg.norm(N @ x - rhs, ord=np.inf))
    if not np.isfinite(x).all() or residual > 1e-5 * max(1.0, np.linalg.norm(rhs, ord=np.inf)):
        raise ValueError(f"normal equation solve failed; residual={residual:g}")
    return x, n, lu


def _lu_diagonal_condition(lu) -> float:
    """Cheap SuperLU warning metric based on U diagonal magnitudes.

    This is deliberately a conservative diagnostic trigger, not a regularizer.
    QR/SVD remains the authoritative rank path when it fires.
    """

    diag_u = np.abs(np.diag(lu.U.toarray()))
    diag_l = np.abs(np.diag(lu.L.toarray()))
    if len(diag_u) == 0 or np.any(diag_u == 0) or np.any(diag_l == 0):
        return float("inf")
    return float(np.max(diag_u) / np.min(diag_u))


def _singular_bounds(A, eps: float):
    n = min(A.shape)
    if n == 0:
        return None, None, False
    try:
        if n <= 120:
            s = np.linalg.svd(A.toarray(), compute_uv=False)
            return float(np.min(s)), float(np.max(s)), True
        k = min(6, n - 1)
        if k < 1:
            dense = A.toarray()
            s = np.linalg.svd(dense, compute_uv=False)
            return float(np.min(s)), float(np.max(s)), True
        low = svds(A, k=k, return_singular_vectors=False, which="SM", tol=1e-10)
        high = svds(A, k=k, return_singular_vectors=False, which="LM", tol=1e-10)
        return float(np.min(low)), float(np.max(high)), True
    except Exception:
        return None, None, False


def _qr_solve(A, b, all_free_ids, local_free_ids, tol: float, dense_limit: int):
    n = A.shape[1]
    if n <= dense_limit:
        Ad = A.toarray()
        Q, R, perm = dense_qr(Ad, mode="economic", pivoting=True)
        diag = np.abs(np.diag(R))
        rank = int(np.count_nonzero(diag > tol * (diag[0] if len(diag) else 1.0)))
        if rank < n:
            null = _nullspace_from_qr(R, perm, local_free_ids)
            return None, rank, null, False
        rhs_q = Q[:, :n].T @ b if A.shape[0] >= n else np.linalg.lstsq(Ad, b, rcond=None)[0]
        xperm = np.linalg.solve(R[:n, :] if R.shape[0] >= n else R, rhs_q) if A.shape[0] >= n else rhs_q
        x = np.empty(n)
        x[perm[:n]] = xperm
        return x, rank, None, True

    try:
        import sparseqr  # type: ignore
        Q, R, E, rank = sparseqr.qr(A)
        if int(rank) < n:
            null_info = _nullspace_diagnostic(A, local_free_ids, tol, dense_limit)
            return None, int(rank), null_info, False
        result = sparseqr.solve(A, b, tolerance=tol)
        return np.asarray(result).ravel(), int(rank), None, True
    except ImportError:
        return None, 0, {
            "dimension": n,
            "message": "Large ill-conditioned component requires QR diagnostics; install sparseqr or reduce dense_qr_limit. No heights emitted.",
            "point_ids": local_free_ids[:20],
        }, False


def _nullspace_from_qr(R, perm, point_ids: list[str]) -> dict:
    n = R.shape[1]
    diag = np.abs(np.diag(R))
    tol = 1e-10 * (diag[0] if len(diag) else 1.0)
    null_cols = [i for i, d in enumerate(diag) if d <= tol]
    examples = []
    for col in null_cols[:3]:
        original = int(perm[col])
        if original < len(point_ids):
            examples.append(point_ids[original])
    return {"dimension": len(null_cols), "point_ids": examples, "message": "QR nullspace identifies unconstrained height modes"}


def _nullspace_diagnostic(A, point_ids: list[str], eps: float, dense_limit: int) -> dict:
    n = A.shape[1]
    if n <= dense_limit:
        u, s, vt = np.linalg.svd(A.toarray(), full_matrices=True)
        null_count = int(np.count_nonzero(s <= eps * max(s[0] if len(s) else 1.0, 1.0)))
        vectors = vt[-null_count:] if null_count else []
        example = [point_ids[i] for i, v in enumerate(vectors[0] if vectors else []) if abs(v) > eps][:20]
        return {"dimension": null_count, "point_ids": example, "message": "SVD/QR nullspace; datum constraints insufficient"}
    try:
        # One approximate smallest right singular vector is enough to stop
        # publication and identify a problematic mode.
        u, s, vt = svds(A, k=1, which="SM", tol=eps)
        vec = vt[0]
        return {"dimension": "at_least_one", "point_ids": [point_ids[i] for i, v in enumerate(vec) if abs(v) > eps][:20], "sigma": float(s[0])}
    except Exception as exc:
        return {"dimension": "unknown", "point_ids": point_ids[:20], "message": f"nullspace estimation failed: {exc}"}


def _first_nullspace(diagnostics):
    for d in diagnostics:
        if d.get("nullspace"):
            null = dict(d["nullspace"])
            null.setdefault("component_index", d["component_index"])
            null.setdefault("error_code", d.get("error_code", "RANK_DEFICIENT"))
            return null
    return None

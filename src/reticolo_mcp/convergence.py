"""Branch-aware harmonic convergence for RETICOLO MCP.

Runs progressive Fourier-order scans, locates and tracks resonance
branches, and reports convergence status.

Orders increase progressively: nn=[5,7,9,11,13,15,...]
For each order:
  1. Coarse scan to locate all candidate peaks.
  2. Narrow fine scan around each peak bracketing both half-height crossings.
  3. Compare peak wavelength, amplitude, FWHM to previous order.
  4. Stop when tolerances are met across all tracked branches.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from .sweep import analyze_sweep, run_sweep


def run_convergence(
    engine: Any,
    *,
    nn_start: int = 5,
    nn_max: int = 21,
    nn_step: int = 2,
    coarse_start: float,
    coarse_end: float,
    coarse_step: float = 0.01,
    fine_half_width: float = 0.02,
    fine_step: float = 0.002,
    D: list[float],
    textures: list[Any],
    profil: dict[str, list],
    polarization: int = 1,
    output_dir: str | Path,
    config_label: str = "",
    tol_wl_um: float = 0.002,
    tol_A: float = 0.01,
    tol_fwhm_nm: float = 1.0,
) -> dict[str, Any]:
    """Run progressive harmonic convergence over nn orders.

    Returns a summary with per-order peaks, FWHM, convergence status,
    and final converged peaks.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    orders: list[dict[str, Any]] = []

    prev_peaks: list[dict[str, Any]] = []
    next_branch_index = 1

    for nn_val in range(nn_start, nn_max + 1, nn_step):
        nn = [nn_val, nn_val]
        stage_label = f"{config_label}_nn{nn_val}"
        csv_path = output_dir / f"nn{nn_val:02d}.csv"
        summ_path = output_dir / f"nn{nn_val:02d}_summary.json"

        # Coarse scan
        coarse_wls = _arange(coarse_start, coarse_end, coarse_step)
        run_sweep(
            engine=engine,
            wls_um=coarse_wls, nn=nn, D=D,
            textures=textures, profil=profil,
            polarization=polarization,
            config_id=stage_label,
            csv_path=str(csv_path),
            resume=False,
        )

        analysis = analyze_sweep(csv_path)
        candidates = analysis.get("peaks", []) + analysis.get("boundary_maxima", [])

        # For each interior peak, run a fine scan
        fine_peaks: list[dict[str, Any]] = []
        for pk in analysis.get("peaks", []):
            fine_csv = output_dir / f"nn{nn_val:02d}_wl{pk['wl_um']:.4f}.csv"
            fine_wls = _arange(
                pk["wl_um"] - fine_half_width,
                pk["wl_um"] + fine_half_width,
                fine_step,
            )
            run_sweep(
                engine=engine,
                wls_um=fine_wls, nn=nn, D=D,
                textures=textures, profil=profil,
                polarization=polarization,
                config_id=f"{stage_label}_fine",
                csv_path=str(fine_csv),
                resume=False,
            )
            fine_analysis = analyze_sweep(fine_csv)
            fine_pks = fine_analysis.get("peaks", [])
            if fine_pks:
                best = max(fine_pks, key=lambda p: p["A"])
                fwhm = _estimate_fwhm(fine_csv)
                best["fwhm_nm"] = fwhm
                best["Q"] = best["wl_um"] / (fwhm / 1000) if fwhm and fwhm > 0 else None
                best["baseline_rule"] = "absolute_zero"
                fine_peaks.append(best)

        converged, next_branch_index = _compare_peak_sets(
            prev_peaks, fine_peaks, next_branch_index=next_branch_index,
            tol_wl_um=tol_wl_um, tol_A=tol_A,
            tol_fwhm_nm=tol_fwhm_nm,
        )

        order_summary = {
            "nn": [nn_val, nn_val],
            "csv": str(csv_path),
            "analysis": analysis,
            "fine_peaks": fine_peaks,
            "converged_peaks": converged,
        }

        import json
        summ_path.write_text(json.dumps(order_summary, indent=2, default=str),
                             encoding="utf-8")

        orders.append(order_summary)
        prev_peaks = converged

    final_peaks = orders[-1]["converged_peaks"] if orders else []
    convergence_reached = bool(
        len(orders) >= 2 and final_peaks
        and all(pk["convergence"] == "converged" for pk in final_peaks)
    )

    return {
        "status": "converged" if convergence_reached else "convergence_not_reached",
        "orders": orders,
        "nn_range": [nn_start, nn_max, nn_step],
        "runtime_s": round(time.time() - t0, 1),
        "output_dir": str(output_dir),
    }


def _compare_peak_sets(
    previous: list[dict[str, Any]], current: list[dict[str, Any]], *,
    next_branch_index: int, tol_wl_um: float, tol_A: float,
    tol_fwhm_nm: float, max_match_shift_um: float = 0.05,
) -> tuple[list[dict[str, Any]], int]:
    """Match branches one-to-one and require center, amplitude, and width."""
    candidates = sorted(
        (
            (abs(cur["wl_um"] - prev["wl_um"]), cur_i, prev_i)
            for cur_i, cur in enumerate(current)
            for prev_i, prev in enumerate(previous)
            if abs(cur["wl_um"] - prev["wl_um"]) <= max_match_shift_um
        ),
        key=lambda item: (item[0], item[1], item[2]),
    )
    matches: dict[int, int] = {}
    used_previous: set[int] = set()
    for _distance, current_index, previous_index in candidates:
        if current_index in matches or previous_index in used_previous:
            continue
        matches[current_index] = previous_index
        used_previous.add(previous_index)

    result: list[dict[str, Any]] = []
    for current_index, peak in enumerate(current):
        entry = {
            "wl_um": peak["wl_um"], "A": peak["A"],
            "R": peak.get("R"), "T": peak.get("T"),
            "fwhm_nm": peak.get("fwhm_nm"), "Q": peak.get("Q"),
            "baseline_rule": peak.get("baseline_rule", "absolute_zero"),
        }
        previous_index = matches.get(current_index)
        if previous_index is None:
            entry["branch_id"] = f"branch-{next_branch_index:03d}"
            next_branch_index += 1
            entry["convergence"] = "new"
            result.append(entry)
            continue

        prior = previous[previous_index]
        entry["branch_id"] = prior.get("branch_id", f"branch-{previous_index + 1:03d}")
        delta_wl = abs(peak["wl_um"] - prior["wl_um"])
        delta_a = abs(peak["A"] - prior["A"])
        current_width = peak.get("fwhm_nm")
        previous_width = prior.get("fwhm_nm")
        delta_width = (
            abs(current_width - previous_width)
            if current_width is not None and previous_width is not None else None
        )
        entry.update({
            "delta_wl_um": delta_wl,
            "delta_A": delta_a,
            "delta_fwhm_nm": delta_width,
        })
        entry["convergence"] = (
            "converged"
            if delta_wl <= tol_wl_um and delta_a <= tol_A
            and delta_width is not None and delta_width <= tol_fwhm_nm
            else "partial"
        )
        result.append(entry)
    return result, next_branch_index


def _estimate_fwhm(
    csv_path: Path, *, baseline_rule: str = "absolute_zero",
) -> float | None:
    """Estimate FWHM only when both sides of the actual peak are bracketed.

    ``absolute_zero`` uses A_peak/2. ``edge_min`` uses the lower scan edge as
    a declared local baseline. The nearest crossing on each side of the global
    peak is used; row-list midpoint is never a proxy for peak position.
    """
    rows = []
    try:
        import csv
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") != "ok":
                    continue
                try:
                    rows.append((float(row["wl_um"]), float(row["A_balance"])))
                except (ValueError, KeyError):
                    pass
    except (OSError, csv.Error):
        return None

    if len(rows) < 3:
        return None

    rows.sort()
    peak_index = max(range(len(rows)), key=lambda i: rows[i][1])
    if peak_index == 0 or peak_index == len(rows) - 1:
        return None
    max_A = rows[peak_index][1]
    if max_A <= 0:
        return None
    if baseline_rule == "absolute_zero":
        baseline = 0.0
    elif baseline_rule == "edge_min":
        baseline = min(rows[0][1], rows[-1][1])
    else:
        raise ValueError(f"unsupported baseline_rule: {baseline_rule}")
    if baseline >= max_A:
        return None
    threshold = baseline + (max_A - baseline) / 2

    left = _nearest_crossing(rows, threshold, range(peak_index - 1, -1, -1))
    right = _nearest_crossing(rows, threshold, range(peak_index, len(rows) - 1))
    if left is None or right is None or right <= left:
        return None
    return (right - left) * 1000  # um → nm


def _nearest_crossing(
    rows: list[tuple[float, float]], threshold: float, indices: Any,
) -> float | None:
    for i in indices:
        w1, a1 = rows[i]
        w2, a2 = rows[i + 1]
        low, high = sorted((a1, a2))
        if not low <= threshold <= high:
            continue
        if a1 == a2:
            return w1 if a1 == threshold else None
        return w1 + (threshold - a1) * (w2 - w1) / (a2 - a1)
    return None


def _arange(start: float, stop: float, step: float) -> list[float]:
    result = []
    val = start
    while val <= stop + step / 2:
        result.append(round(val, 9))
        val += step
    return result

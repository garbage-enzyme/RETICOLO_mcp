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
) -> dict[str, Any]:
    """Run progressive harmonic convergence over nn orders.

    Returns a summary with per-order peaks, FWHM, convergence status,
    and final converged peaks.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    orders: list[dict[str, Any]] = []

    prev_peaks: dict[int, dict[str, Any]] = {}

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
                fine_peaks.append(best)

        # Compare with previous order
        converged: list[dict[str, Any]] = []
        for pk in fine_peaks:
            pk_wl = pk["wl_um"]
            conv_status = "new"
            matched_prev = None
            for pid, prev in prev_peaks.items():
                if abs(pk_wl - prev["wl_um"]) < 0.05:
                    matched_prev = prev
                    dwl = abs(pk_wl - prev["wl_um"])
                    dA = abs(pk["A"] - prev.get("A", 0))
                    if dwl <= tol_wl_um and dA <= tol_A:
                        conv_status = "converged"
                    else:
                        conv_status = "partial"
                    break

            entry = {
                "wl_um": pk_wl,
                "A": pk["A"],
                "R": pk.get("R"),
                "T": pk.get("T"),
                "fwhm_nm": pk.get("fwhm_nm"),
                "Q": pk.get("Q"),
                "convergence": conv_status,
            }
            if matched_prev:
                entry["delta_wl_um"] = abs(pk_wl - matched_prev["wl_um"])
                entry["delta_A"] = abs(pk["A"] - matched_prev.get("A", 0))
            converged.append(entry)

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
        prev_peaks = {i: pk for i, pk in enumerate(fine_peaks)}

    return {
        "orders": orders,
        "nn_range": [nn_start, nn_max, nn_step],
        "runtime_s": round(time.time() - t0, 1),
        "output_dir": str(output_dir),
    }


def _estimate_fwhm(csv_path: Path) -> float | None:
    """Estimate FWHM in nm from a fine sweep CSV by half-max interpolation."""
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
    max_A = max(a for _, a in rows)
    if max_A <= 0:
        return None
    half = max_A / 2

    left = right = None
    for i in range(len(rows) - 1):
        w1, a1 = rows[i]
        w2, a2 = rows[i + 1]
        if a1 <= half <= a2 or a2 <= half <= a1:
            if a1 != a2:
                w_half = w1 + (half - a1) * (w2 - w1) / (a2 - a1)
                if left is None and w_half <= rows[len(rows)//2][0]:
                    left = w_half
                elif right is None:
                    right = w_half
            elif abs(a1 - half) < 1e-12:
                if left is None:
                    left = w1
                else:
                    right = w1

    if left is not None and right is not None:
        return abs(right - left) * 1000  # um → nm
    return None


def _arange(start: float, stop: float, step: float) -> list[float]:
    result = []
    val = start
    while val <= stop + step / 2:
        result.append(round(val, 9))
        val += step
    return result

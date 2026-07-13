"""Resumable wavelength sweep for RETICOLO MCP.

One row per wavelength, flushed and fsynced immediately.
Supports resume: reads existing CSV, skips rows with matching
config_hash (canonical) AND status=ok.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


def run_sweep(
    engine: Any,
    *,
    wls_um: list[float],
    nn: list[int],
    D: float | list[float],
    textures: list[Any],
    profil: dict[str, list],
    polarization: int = 1,
    config_id: str = "",
    config_hash: str = "",
    csv_path: str | Path,
    resume: bool = True,
) -> dict[str, Any]:
    """Run a wavelength sweep with per-row CSV persistence.

    Args:
        engine: REticoloEngine instance (must already be started).
        wls_um: Sorted list of wavelengths in microns.
        nn: Fourier orders [nx, ny].
        D: Lattice period(s).
        textures: RETICOLO texture definitions.
        profil: Layer thickness profile.
        polarization: 1 for TE, -1 for TM.
        config_id: Human-readable label (optional).
        config_hash: Canonical SHA-256 of physical inputs.
                     Resume matches on this, not config_id alone.
        csv_path: Path to output CSV file.
        resume: If True, skip rows already solved with matching config_hash.

    Returns:
        {total, solved, skipped, errors, csv_path, runtime_s, config_hash}
    """
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    D_list = [float(D)] if isinstance(D, (int, float)) else [float(v) for v in D]

    resume_key = config_hash or config_id or ""
    skipped: set[float] = set()
    if resume and csv_path.exists():
        skipped = _read_completed(csv_path, resume_key)

    pending = [w for w in sorted(wls_um) if w not in skipped]
    if not pending:
        return {"total": len(wls_um), "solved": 0, "skipped": len(skipped),
                "errors": 0, "csv_path": str(csv_path), "runtime_s": 0,
                "config_hash": config_hash, "status": "all_skipped"}

    file_exists = csv_path.exists()
    t0 = time.time()
    solved = 0
    errors = 0

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "wl_um", "nn_x", "nn_y", "R", "T", "A_balance",
                "passive", "solve_time_s", "status", "error",
                "config_hash", "config_id", "polarization", "timestamp",
            ])

        for wl in pending:
            row_time = time.time()
            result = engine.solve_point(
                wl_um=wl, D=D_list, nn=nn,
                textures=textures, profil=profil,
                polarization=polarization, config_id=config_id,
            )

            writer.writerow([
                f"{wl:.6f}",
                result.get("nn", [nn[0], nn[1]])[0],
                result.get("nn", [nn[0], nn[1]])[1],
                f"{result.get('R', 0):.12f}" if result["status"] == "ok" else "",
                f"{result.get('T', 0):.12f}" if result["status"] == "ok" else "",
                f"{result.get('A_balance', 0):.12f}" if result["status"] == "ok" else "",
                str(result.get("passive", "")),
                f"{float(result.get('solve_time_s', time.time() - row_time)):.3f}",
                result["status"],
                result.get("error", ""),
                config_hash,
                config_id,
                str(result.get("polarization", "")),
                time.strftime("%Y-%m-%dT%H:%M:%S"),
            ])
            f.flush()
            os.fsync(f.fileno())

            if result["status"] == "ok":
                solved += 1
            else:
                errors += 1

    return {
        "total": len(wls_um),
        "solved": solved,
        "skipped": len(skipped),
        "errors": errors,
        "csv_path": str(csv_path),
        "runtime_s": round(time.time() - t0, 1),
        "config_hash": config_hash,
        "status": "completed" if errors == 0 else "completed_with_errors",
    }


# ------------------------------------------------------------------
# sweep analysis — peak detection, boundary marking
# ------------------------------------------------------------------


def analyze_sweep(csv_path: Path) -> dict[str, Any]:
    """Read a completed sweep CSV and return peak summary.

    Marks boundary points (first/last wavelength) explicitly — they
    cannot be accepted as physical peaks without bracket evidence.
    """
    rows: list[dict[str, Any]] = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") != "ok":
                    continue
                try:
                    rows.append({
                        "wl": float(row["wl_um"]),
                        "A": float(row["A_balance"]),
                        "R": float(row["R"]),
                        "T": float(row["T"]),
                    })
                except (ValueError, KeyError):
                    pass
    except (OSError, csv.Error):
        return {"error": "cannot_read_csv", "path": str(csv_path)}

    if not rows:
        return {"points": 0, "peaks": [], "boundary_maxima": []}

    rows.sort(key=lambda r: r["wl"])
    wls = [r["wl"] for r in rows]
    vals = [r["A"] for r in rows]

    peaks: list[dict[str, Any]] = []
    boundary_maxima: list[dict[str, Any]] = []

    for i in range(len(vals)):
        is_boundary = (i == 0 or i == len(vals) - 1)
        is_local_max = False
        if i > 0 and i < len(vals) - 1:
            if vals[i] > vals[i - 1] and vals[i] > vals[i + 1]:
                is_local_max = True
        elif is_boundary:
            is_local_max = vals[i] > vals[1] if i == 0 else vals[i] > vals[-2]

        if is_local_max:
            entry = {
                "wl_um": wls[i],
                "A": vals[i],
                "R": rows[i]["R"],
                "T": rows[i]["T"],
                "boundary": is_boundary,
                "index": i,
            }
            if is_boundary:
                boundary_maxima.append(entry)
            else:
                peaks.append(entry)

    return {
        "points": len(rows),
        "wl_range": [wls[0], wls[-1]],
        "peaks": peaks,
        "boundary_maxima": boundary_maxima,
    }


def _read_completed(csv_path: Path, resume_key: str) -> set[float]:
    """Return wavelengths already solved with matching resume identity.

    Matches on config_hash if present, otherwise config_id.
    """
    if not resume_key:
        return set()
    completed: set[float] = set()
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            hash_col = "config_hash" if "config_hash" in (reader.fieldnames or []) else None
            id_col = "config_id" if "config_id" in (reader.fieldnames or []) else None

            for row in reader:
                key = ""
                if hash_col and row.get(hash_col):
                    key = row[hash_col]
                elif id_col and row.get(id_col):
                    key = row[id_col]
                if key != resume_key:
                    continue
                if row.get("status") != "ok":
                    continue
                try:
                    completed.add(float(row["wl_um"]))
                except (ValueError, KeyError):
                    pass
    except (OSError, csv.Error):
        pass
    return completed

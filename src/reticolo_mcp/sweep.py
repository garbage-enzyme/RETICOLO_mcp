"""Resumable wavelength sweep for RETICOLO MCP.

One row per wavelength, flushed and fsynced immediately.
Supports resume: reads existing CSV, skips rows with matching
config_id and status=ok.
"""

from __future__ import annotations

import csv
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
    config_id: str,
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
        config_id: Stable configuration identity. Rows with a different
                   config_id are skipped/replaced on resume.
        csv_path: Path to output CSV file.
        resume: If True, skip rows already solved with the same config_id.

    Returns:
        {total, solved, skipped, errors, csv_path, runtime_s}
    """
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    D_list = [float(D)] if isinstance(D, (int, float)) else [float(v) for v in D]

    skipped: set[float] = set()
    if resume and csv_path.exists():
        skipped = _read_completed(csv_path, config_id)

    pending = [w for w in sorted(wls_um) if w not in skipped]
    if not pending:
        return {"total": len(wls_um), "solved": 0, "skipped": len(skipped),
                "errors": 0, "csv_path": str(csv_path), "runtime_s": 0,
                "status": "all_skipped"}

    file_exists = csv_path.exists()
    t0 = time.time()
    solved = 0
    errors = 0

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "wl_um", "nn_x", "nn_y", "R", "T", "A", "energy_sum",
                "passive", "solve_time_s", "status", "error",
                "config_id", "timestamp",
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
                f"{result.get('A', 0):.12f}" if result["status"] == "ok" else "",
                f"{result.get('energy_sum', 0):.12f}" if result["status"] == "ok" else "",
                str(result.get("passive", "")),
                f"{result.get('solve_time_s', time.time() - row_time):.3f}",
                result["status"],
                result.get("error", ""),
                config_id,
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
        "status": "completed" if errors == 0 else "completed_with_errors",
    }


def _read_completed(csv_path: Path, config_id: str) -> set[float]:
    """Return wavelengths already solved with matching config_id."""
    completed: set[float] = set()
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("config_id") != config_id:
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

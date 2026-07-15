"""RETICOLO MCP server — MCP interface for RETICOLO V10 RCWA solver.

Start with: python -m reticolo_mcp.server
"""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import __version__
from .config import (
    EXPERIMENTAL_ENABLED,
    MAX_CONFIG_ID_LEN,
    MAX_FOURIER_ORDER,
    MAX_JOB_POINTS,
    MAX_TEXTURES,
    RETICOLO_DIR,
)
from .engine import REticoloEngine
from .lease import lease_status as _lease_status
from .sweep import run_sweep
from .config_hash import compute_config_hash
from . import jobs
from .convergence import run_convergence
from .field_export import export_field
from .capabilities import capability_receipt

mcp = FastMCP("reticolo-mcp")
engine = REticoloEngine(RETICOLO_DIR)


# ------------------------------------------------------------------
# input validation helpers (P0-4)
# ------------------------------------------------------------------

def _validate_solve_inputs(
    wl_um: float,
    D: list[float],
    nn: list[int],
    textures: list,
    profil: dict,
    polarization: int,
    config_id: str,
) -> dict | None:
    """Return error dict if inputs fail validation, else None."""
    if not 0.1 < float(wl_um) < 100.0:
        return {"status": "error", "error_code": "invalid_wl",
                "detail": f"wavelength out of range: {wl_um}"}
    if len(D) not in (1, 2):
        return {"status": "error", "error_code": "invalid_D",
                "detail": "D must be [Px] or [Px, Py]"}
    if not all(v > 0 for v in D):
        return {"status": "error", "error_code": "invalid_D",
                "detail": "lattice periods must be positive"}
    if len(nn) != 2 or not all(isinstance(n, int) and n >= 1 for n in nn):
        return {"status": "error", "error_code": "invalid_nn",
                "detail": "nn must be [nx, ny] with positive integers"}
    if any(n > MAX_FOURIER_ORDER for n in nn):
        return {"status": "error", "error_code": "order_limit_exceeded",
                "detail": f"maximum Fourier order is {MAX_FOURIER_ORDER}"}
    if len(textures) > MAX_TEXTURES:
        return {"status": "error", "error_code": "too_many_textures",
                "detail": f"max {MAX_TEXTURES} textures, got {len(textures)}"}
    if polarization == -1:
        return {"status": "error", "error_code": "unsupported_polarization",
                "detail": "TM result-channel mapping is not release accepted"}
    if polarization != 1:
        return {"status": "error", "error_code": "invalid_polarization",
                "detail": "verified public polarization is currently 1 (TE)"}
    if len(config_id) > MAX_CONFIG_ID_LEN:
        return {"status": "error", "error_code": "config_id_too_long",
                "detail": f"config_id max {MAX_CONFIG_ID_LEN} chars"}
    heights = profil.get("heights", [])
    indices = profil.get("indices", [])
    if not heights or not indices:
        return {"status": "error", "error_code": "invalid_profil",
                "detail": "profil must have non-empty heights and indices"}
    if len(heights) < 2:
        return {"status": "error", "error_code": "invalid_profil",
                "detail": "heights must have at least 2 entries [top, ..., 0]"}
    if heights[-1] != 0.0:
        return {"status": "error", "error_code": "invalid_profil",
                "detail": "last height must be 0 (semi-infinite substrate)"}
    if len(heights) != len(indices):
        return {"status": "error", "error_code": "invalid_profil",
                "detail": "heights and indices must have same length"}
    return None


# ------------------------------------------------------------------
# tools
# ------------------------------------------------------------------

@mcp.tool()
def reticolo_capabilities() -> dict:
    """Return solver-free tool maturity, schema, and deployment identity."""
    return capability_receipt(mcp._tool_manager._tools.keys())

@mcp.tool()
def reticolo_start() -> dict:
    """Start MATLAB engine and initialize RETICOLO V10.

    Applies M0 disk-safety: vmax=inf (no scratch .mat files),
    MATLAB temp redirected, working directory on scratch volume.
    Returns engine status including connection state, uptime, and RETICOLO path.
    """
    return engine.start()


@mcp.tool()
def reticolo_stop() -> dict:
    """Stop the MATLAB engine, clean scratch files, and release license.

    Safe to call when already stopped.
    """
    return engine.stop()


@mcp.tool()
def reticolo_status() -> dict:
    """Report MATLAB engine state without side effects.

    Returns connected/stopped, uptime, RETICOLO path, lease state.
    Does not start MATLAB or mutate any state.
    """
    return engine.status()


@mcp.tool()
def reticolo_solve_point(
    wl_um: float,
    D: list[float],
    nn: list[int],
    textures: list,
    profil: dict,
    polarization: int = 1,
    config_id: str = "",
) -> dict:
    """Solve a single wavelength point with RETICOLO RCWA.

    Args:
        wl_um: Wavelength in microns (0.1 < wl < 100).
        D: Lattice period(s) in um — [Px] for 1D, [Px, Py] for 2D.
        nn: Fourier truncation orders [nx, ny] (positive integers).
        textures: Layer materials. Each entry is a refractive index (number)
                  or, for patterned layers, a list [bg_n, [cx,cy,dx,dy,n,k], ...].
        profil: {"heights": [z0, z1, ..., 0], "indices": [i0, i1, ...]}.
        polarization: 1 for TE, -1 for TM.
        config_id: Optional provenance tag (max 128 chars).

    Returns:
        {status, wl_um, nn, R, T, A_balance, passive, solve_time_s, config_id}
    """
    err = _validate_solve_inputs(
        wl_um=wl_um, D=D, nn=nn, textures=textures, profil=profil,
        polarization=polarization, config_id=config_id,
    )
    if err:
        return err

    return engine.solve_point(
        wl_um=float(wl_um),
        D=D,
        nn=[int(nn[0]), int(nn[1])] if len(nn) >= 2 else [int(nn[0]), int(nn[0])],
        textures=textures,
        profil=profil,
        polarization=int(polarization),
        config_id=config_id,
    )


@mcp.tool()
def solver_status() -> dict:
    """Report solver lease state without starting MATLAB.

    Returns reticolo lease status, COMSOL MCP lease status,
    collision detection, and whether the solver is ready.
    Read-only. Does not start MATLAB or COMSOL.
    """
    return _lease_status()


@mcp.tool()
def reticolo_sweep(
    wls_um: list[float],
    nn: list[int],
    D: list[float],
    textures: list,
    profil: dict,
    csv_path: str,
    config_id: str = "",
    polarization: int = 1,
    resume: bool = True,
) -> dict:
    """Run a resumable wavelength sweep with per-row CSV persistence.

    Each wavelength is solved via solve_point and written immediately to CSV
    with flush+fsync. On resume, rows with matching config_id and status=ok
    are skipped.

    Args:
        wls_um: Sorted list of wavelengths in microns.
        nn: Fourier orders [nx, ny].
        D: Lattice period(s) — [Px] or [Px, Py].
        textures: Layer material definitions.
        profil: {"heights": [...], "indices": [...]}.
        csv_path: Absolute path for the output CSV file.
        config_id: Provenance tag (max 128 chars). Resume matches on this.
        polarization: 1 for TE, -1 for TM.
        resume: If True, skip already-solved rows.

    Returns:
        {total, solved, skipped, errors, csv_path, runtime_s, status}
    """
    if engine.status()["status"] != "connected":
        return {"status": "error", "error_code": "engine_not_started"}

    err = _validate_solve_inputs(
        wl_um=wls_um[0] if wls_um else 5.0,
        D=D, nn=nn, textures=textures, profil=profil,
        polarization=polarization, config_id=config_id,
    )
    if err:
        return err

    config_hash = compute_config_hash(
        schema_version="1",
        reticolo_version="V10",
        wls_um=[float(w) for w in wls_um],
        D=[float(v) for v in D],
        nn=[int(nn[0]), int(nn[1])],
        textures=textures,
        profil=profil,
        polarization=int(polarization),
    )

    return run_sweep(
        engine=engine,
        wls_um=[float(w) for w in wls_um],
        nn=[int(nn[0]), int(nn[1])] if len(nn) >= 2 else [int(nn[0]), int(nn[0])],
        D=D,
        textures=textures,
        profil=profil,
        polarization=int(polarization),
        config_id=config_id,
        config_hash=config_hash,
        csv_path=csv_path,
        resume=resume,
    )


@mcp.tool()
def job_submit(
    wls_um: list[float],
    D: list[float],
    nn: list[int],
    textures: list,
    profil: dict,
    polarization: int = 1,
    config_label: str = "",
    mode: str = "memory",
) -> dict:
    """Submit a durable staged-sweep job. Returns immediately with job_id.

    The worker runs independently in a detached process. Use job_status
    and job_tail to monitor progress.
    """
    if not wls_um:
        return {"status": "error", "error_code": "empty_job"}
    if len(wls_um) > MAX_JOB_POINTS:
        return {
            "status": "error", "error_code": "too_many_points",
            "max_points": MAX_JOB_POINTS, "requested_points": len(wls_um),
        }
    if mode not in {"memory", "scratch"}:
        return {"status": "error", "error_code": "invalid_mode"}
    try:
        nonfinite = any(not math.isfinite(float(w)) for w in wls_um)
    except (TypeError, ValueError):
        return {"status": "error", "error_code": "invalid_wavelength"}
    if nonfinite:
        return {"status": "error", "error_code": "nonfinite_wavelength"}
    for wl in wls_um:
        err = _validate_solve_inputs(
            wl_um=wl, D=D, nn=nn, textures=textures, profil=profil,
            polarization=polarization, config_id=config_label,
        )
        if err:
            return err

    job_id = f"job-{uuid.uuid4().hex[:12]}"
    attempt_id = uuid.uuid4().hex
    spec = jobs.create_job_spec(
        wls_um=wls_um, D=D, nn=nn, textures=textures, profil=profil,
        polarization=polarization, config_label=config_label, mode=mode,
    )
    try:
        jobs.write_spec(job_id, spec)
    except ValueError as exc:
        return {"status": "error", "error_code": "spec_rejected",
                "detail": str(exc)}

    jobs.write_state(job_id, {
        "status": "submitted", "attempt": 1, "attempt_id": attempt_id,
    })
    jobs.append_event(job_id, {
        "event": "job_submitted", "attempt": 1, "attempt_id": attempt_id,
    })

    worker_pid = _spawn_worker(job_id)

    return {"status": "ok", "job_id": job_id,
            "config_hash": spec["config_hash"],
            "total_points": len(wls_um), "attempt_id": attempt_id,
            "launcher_pid": worker_pid}


@mcp.tool()
def job_status(job_id: str) -> dict:
    """Report durable job state. Read-only, no side effects."""
    try:
        state = jobs.read_state(job_id)
        spec = jobs.read_spec(job_id)
    except ValueError:
        return {"status": "error", "error_code": "invalid_job_id"}
    if state is None:
        return {"status": "error", "error_code": "job_not_found"}
    return {
        "job_id": job_id,
        "state": state,
        "total_points": len(spec["wls_um"]) if spec else 0,
        "config_hash": spec.get("config_hash", "") if spec else "",
    }


@mcp.tool()
def job_tail(job_id: str, n: int = 20) -> dict:
    """Return the last N events from a job. Read-only."""
    try:
        events = jobs.read_events(job_id, tail=n)
        state = jobs.read_state(job_id)
    except ValueError as exc:
        code = "invalid_tail" if "tail" in str(exc) else "invalid_job_id"
        return {"status": "error", "error_code": code}
    return {"job_id": job_id, "events": events,
            "state": state}


@mcp.tool()
def job_cancel(job_id: str) -> dict:
    """Request cancellation of a running job.

    The worker checks for cancellation between solve points.
    This is cooperative; it cannot interrupt a running res1 call.
    """
    try:
        state = jobs.read_state(job_id)
    except ValueError:
        return {"status": "error", "error_code": "invalid_job_id"}
    if state is None:
        return {"status": "error", "error_code": "job_not_found"}
    transition = jobs.transition_state(
        job_id, allowed_from={"running", "starting"},
        attempt_id=state.get("attempt_id"),
        updates={"status": "cancel_requested"},
    )
    if not transition["updated"]:
        current = transition.get("state") or state
        return {"status": "error", "error_code": "not_running",
                "current_status": current.get("status")}
    jobs.append_event(job_id, {
        "event": "cancel_requested",
        "attempt": state.get("attempt"),
        "attempt_id": state.get("attempt_id"),
    })
    return {"status": "ok", "job_id": job_id, "cancel_requested": True}


@mcp.tool()
def job_resume(job_id: str) -> dict:
    """Resume a failed or interrupted job. Starts a new worker."""
    try:
        state = jobs.read_state(job_id)
    except ValueError:
        return {"status": "error", "error_code": "invalid_job_id"}
    if state is None:
        return {"status": "error", "error_code": "job_not_found"}
    if state["status"] == "completed":
        return {"status": "ok", "message": "job already completed",
                "job_id": job_id}

    attempt = int(state.get("attempt", 0)) + 1
    attempt_id = uuid.uuid4().hex
    transition = jobs.transition_state(
        job_id,
        allowed_from={
            "failed", "interrupted", "completed_with_errors",
            "cleanup_uncertain", "resource_refused",
        },
        attempt_id=state.get("attempt_id"),
        updates={
            "status": "submitted", "attempt": attempt,
            "attempt_id": attempt_id,
        },
    )
    if not transition["updated"]:
        current = transition.get("state") or state
        return {"status": "error", "error_code": "cannot_resume",
                "current_status": current.get("status")}
    jobs.append_event(job_id, {
        "event": "job_resumed", "attempt": attempt,
        "attempt_id": attempt_id,
    })

    worker_pid = _spawn_worker(job_id)
    return {
        "status": "ok", "job_id": job_id, "resumed": True,
        "attempt": attempt, "attempt_id": attempt_id,
        "launcher_pid": worker_pid,
    }


def _spawn_worker(job_id: str) -> int:
    """Launch one hidden detached worker and return the launcher PID."""
    worker_script = str(Path(__file__).resolve().parent / "worker.py")
    env = os.environ.copy()
    src_dir = str(Path(__file__).resolve().parent.parent)
    existing_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_path}" if existing_path else src_dir
    proc = subprocess.Popen(
        [sys.executable, worker_script, job_id],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return int(proc.pid)


@mcp.tool()
def reticolo_convergence(
    coarse_start: float,
    coarse_end: float,
    D: list[float],
    nn: list[int],
    textures: list,
    profil: dict,
    polarization: int = 1,
    config_label: str = "",
    coarse_step: float = 0.01,
    fine_half: float = 0.02,
    fine_step: float = 0.002,
    tol_wl: float = 0.002,
    tol_A: float = 0.01,
    tol_fwhm_nm: float = 1.0,
) -> dict:
    """Run progressive harmonic convergence over Fourier orders.

    For each order from nn[0] to nn[1]:
      1. Coarse scan to locate peaks.
      2. Fine scan around each interior peak → FWHM, Q.
      3. Compare with previous order for convergence.

    Requires engine to be connected (call reticolo_start first).
    """
    if not EXPERIMENTAL_ENABLED:
        return {
            "status": "error", "error_code": "experimental_tool_disabled",
            "detail": "set RETICOLO_MCP_ENABLE_EXPERIMENTAL=1 and restart the host",
        }
    numeric = [
        coarse_start, coarse_end, coarse_step, fine_half, fine_step,
        tol_wl, tol_A, tol_fwhm_nm,
    ]
    if not all(math.isfinite(float(value)) for value in numeric):
        return {"status": "error", "error_code": "nonfinite_convergence_input"}
    if coarse_end <= coarse_start:
        return {"status": "error", "error_code": "invalid_convergence_range"}
    if coarse_step <= 0 or fine_half <= 0 or fine_step <= 0:
        return {"status": "error", "error_code": "invalid_convergence_step"}
    if any(value < 0 for value in (tol_wl, tol_A, tol_fwhm_nm)):
        return {"status": "error", "error_code": "invalid_convergence_tolerance"}
    coarse_points = math.floor((coarse_end - coarse_start) / coarse_step) + 1
    fine_points = math.floor((2 * fine_half) / fine_step) + 1
    if coarse_points > MAX_JOB_POINTS or fine_points > MAX_JOB_POINTS:
        return {
            "status": "error", "error_code": "convergence_point_limit_exceeded",
            "max_points_per_stage": MAX_JOB_POINTS,
        }
    err = _validate_solve_inputs(
        wl_um=coarse_start, D=D, nn=nn, textures=textures,
        profil=profil, polarization=polarization, config_id=config_label,
    )
    if err:
        return err
    if engine.status()["status"] != "connected":
        return {"status": "error", "error_code": "engine_not_started"}

    out_dir = Path(tempfile.gettempdir()) / f"ret_conv_{uuid.uuid4().hex[:8]}"

    return run_convergence(
        engine=engine,
        nn_start=nn[0], nn_max=nn[1],
        coarse_start=coarse_start, coarse_end=coarse_end,
        coarse_step=coarse_step,
        fine_half_width=fine_half, fine_step=fine_step,
        D=D, textures=textures, profil=profil,
        polarization=polarization,
        output_dir=out_dir,
        config_label=config_label or "conv",
        tol_wl_um=tol_wl, tol_A=tol_A, tol_fwhm_nm=tol_fwhm_nm,
    )


@mcp.tool()
def reticolo_field_export(
    wl_um: float,
    D: list[float],
    nn: list[int],
    textures: list,
    profil: dict,
    polarization: int = 1,
    component: str = "normE",
    slice_axis: str = "z",
    slice_value: float = 0.0,
    max_points: int = 500_000,
    output_dir: str = "",
    config_label: str = "",
) -> dict:
    """Solve and export electromagnetic field on a slice plane.

    Requires engine to be connected. Returns coordinate bounds,
    field max/min, and writes NPZ + JSON summary if output_dir provided.
    Does NOT return large arrays through MCP — use output_dir.
    """
    if not EXPERIMENTAL_ENABLED:
        return {
            "status": "error", "error_code": "experimental_tool_disabled",
            "detail": "set RETICOLO_MCP_ENABLE_EXPERIMENTAL=1 and restart the host",
        }
    return export_field(
        engine=engine,
        wl_um=wl_um, D=D, nn=nn,
        textures=textures, profil=profil,
        polarization=polarization,
        component=component,
        slice_axis=slice_axis, slice_value=slice_value,
        max_points=max_points,
        output_dir=output_dir or None,
        config_label=config_label,
    )


# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="RETICOLO MCP server")
    parser.add_argument("--version", action="version",
                        version=f"reticolo-mcp {__version__}")
    parser.add_argument("--reticolo-dir", type=str, default=None,
                        help="Path to RETICOLO V10 reticolo_allege_v10 directory")
    args = parser.parse_args()

    if args.reticolo_dir:
        p = Path(args.reticolo_dir)
        if p.is_dir():
            engine._reticolo_dir = p
        else:
            print(f"ERROR: --reticolo-dir not found: {p}", file=sys.stderr)
            sys.exit(1)

    print(f"[reticolo-mcp] v{__version__}  reticolo={engine._reticolo_dir}",
          file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()

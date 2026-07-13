"""RETICOLO MCP server — MCP interface for RETICOLO V10 RCWA solver.

Start with: python -m reticolo_mcp.server
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import __version__
from .config import MAX_CONFIG_ID_LEN, MAX_TEXTURES, RETICOLO_DIR
from .engine import REticoloEngine
from .lease import lease_status as _lease_status
from .sweep import run_sweep
from .config_hash import compute_config_hash

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
    if len(textures) > MAX_TEXTURES:
        return {"status": "error", "error_code": "too_many_textures",
                "detail": f"max {MAX_TEXTURES} textures, got {len(textures)}"}
    if polarization not in (-1, 1):
        return {"status": "error", "error_code": "invalid_polarization",
                "detail": "polarization must be 1 (TE) or -1 (TM)"}
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

    return run_sweep(
        engine=engine,
        wls_um=[float(w) for w in wls_um],
        nn=[int(nn[0]), int(nn[1])] if len(nn) >= 2 else [int(nn[0]), int(nn[0])],
        D=D,
        textures=textures,
        profil=profil,
        polarization=int(polarization),
        config_id=config_id,
        csv_path=csv_path,
        resume=resume,
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

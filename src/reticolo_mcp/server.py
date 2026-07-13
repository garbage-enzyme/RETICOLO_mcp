"""RETICOLO MCP server — MCP interface for RETICOLO V10 RCWA solver.

Start with: python -m reticolo_mcp.server

Profile detection follows the environment variable:
  RETICOLO_MCP_PROFILE = core | full  (default: core)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import __version__
from .config import MAX_CONFIG_ID_LEN, MAX_TEXTURES, RETICOLO_DIR
from .engine import REticoloEngine

mcp = FastMCP("reticolo-mcp")
engine = REticoloEngine(RETICOLO_DIR)


# ------------------------------------------------------------------
# tools
# ------------------------------------------------------------------

@mcp.tool()
def reticolo_start() -> dict:
    """Start MATLAB engine and initialize RETICOLO V10.

    Applies M0 disk-safety: vmax=inf (no scratch .mat files),
    MATLAB temp redirected to D:\\matlab_temp, working directory D:\\reticolo_scratch.
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

    Returns connected/stopped, uptime, RETICOLO path, and disk-safety mode.
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
        wl_um: Wavelength in microns.
        D: Lattice period(s) in um — [Px] for 1D, [Px, Py] for 2D.
        nn: Fourier truncation orders [nx, ny].
        textures: Layer materials. Each entry is a refractive index (number)
                  or, for patterned layers, a list [bg_n, [cx,cy,dx,dy,n,k], ...].
        profil: {"heights": [z0, z1, ..., 0], "indices": [i0, i1, ...]}.
                1-based indices into textures. Last height=0 for semi-inf substrate.
        polarization: 1 for TE, -1 for TM.
        config_id: Optional provenance tag (max 128 chars).

    Returns:
        {status, wl_um, nn, R, T, A, energy_sum, passive, solve_time_s, config_id}
    """
    if len(config_id) > MAX_CONFIG_ID_LEN:
        config_id = config_id[:MAX_CONFIG_ID_LEN]

    if len(textures) > MAX_TEXTURES:
        return {"status": "error", "error_code": "too_many_textures",
                "detail": f"max 32 textures, got {len(textures)}"}

    return engine.solve_point(
        wl_um=float(wl_um),
        D=D,
        nn=[int(nn[0]), int(nn[1])] if len(nn) >= 2 else [int(nn[0]), int(nn[0])],
        textures=textures,
        profil=profil,
        polarization=int(polarization),
        config_id=config_id,
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

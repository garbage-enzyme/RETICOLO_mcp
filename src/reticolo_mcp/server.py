"""RETICOLO MCP server — minimal skeleton.

Start with: python -m reticolo_mcp.server
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .engine import REticoloEngine

HERE = Path(__file__).resolve().parent
RETICOLO_DIR = HERE.parent.parent / "reticolo_v10" / "reticolo_allege_v10"

mcp = FastMCP("reticolo-mcp")
engine = REticoloEngine(RETICOLO_DIR)


@mcp.tool()
def reticolo_start() -> dict:
    """Start the MATLAB engine and initialize RETICOLO V10.

    Returns status including version and whether RETICOLO is ready.
    """
    return engine.start()


@mcp.tool()
def reticolo_stop() -> dict:
    """Stop the MATLAB engine and release the license."""
    return engine.stop()


@mcp.tool()
def reticolo_status() -> dict:
    """Report the current MATLAB engine and RETICOLO state."""
    return engine.status()


@mcp.tool()
def reticolo_solve_point(
    wl_um: float,
    nn_x: int,
    nn_y: int,
    textures: dict,
    profil: dict,
    config_id: str = "",
) -> dict:
    """Solve a single wavelength point with RETICOLO.

    Args:
        wl_um: Wavelength in microns.
        nn_x: Fourier truncation order in x.
        nn_y: Fourier truncation order in y.
        textures: Layer refractive-index definitions.
        profil: Layer thickness profile.
        config_id: Provenance tag for the configuration.

    Returns:
        {R, T, A, energy_sum, nn_x, nn_y, solve_time_s, status, error}
    """
    return engine.solve_point(
        wl_um=wl_um,
        nn_x=int(nn_x),
        nn_y=int(nn_y),
        textures=textures,
        profil=profil,
        config_id=config_id,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

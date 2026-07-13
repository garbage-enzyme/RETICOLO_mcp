"""MATLAB Engine wrapper for RETICOLO V10.

Manages the lifecycle of one MATLAB engine session, enforces the M0 disk-safety
contract, and translates Python -> MATLAB -> Python for solve requests.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any


class REticoloEngine:
    """Owns exactly one MATLAB engine for RETICOLO RCWA computation."""

    def __init__(self, reticolo_dir: Path) -> None:
        self._reticolo_dir = reticolo_dir
        self._engine: Any = None
        self._started_at: float | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> dict:
        """Start MATLAB engine, add RETICOLO to path, apply M0 safety."""
        if self._engine is not None:
            return self.status()

        try:
            import matlab.engine
        except ImportError:
            return {
                "status": "error",
                "reason": "matlab.engine not installed. "
                "Run: pip install <MATLAB_R2025b>/extern/engines/python/",
            }

        if not self._reticolo_dir.is_dir():
            return {
                "status": "error",
                "reason": f"RETICOLO directory not found: {self._reticolo_dir}",
            }

        self._engine = matlab.engine.start_matlab()
        self._started_at = time.time()

        self._engine.addpath(str(self._reticolo_dir), nargout=0)

        self._engine.eval(
            "cd('D:\\reticolo_scratch');", nargout=0
        )
        self._engine.eval(
            "[~, ~] = retio([], inf*1i);", nargout=0
        )

        self._engine.eval("setenv('TMP','D:\\matlab_temp');", nargout=0)
        self._engine.eval("setenv('TEMP','D:\\matlab_temp');", nargout=0)
        self._engine.eval("setenv('TMPDIR','D:\\matlab_temp');", nargout=0)

        self._engine.eval("parm = res0;", nargout=0)
        self._engine.eval("parm.res1.champ = 0;", nargout=0)
        self._engine.eval("parm.res1.trace = 0;", nargout=0)

        return self.status()

    def stop(self) -> dict:
        """Stop the MATLAB engine and clean up."""
        if self._engine is None:
            return {"status": "stopped"}

        try:
            self._engine.eval("retio;", nargout=0)
        except Exception:
            pass

        try:
            self._engine.quit()
        except Exception:
            pass

        self._engine = None
        self._started_at = None
        return {"status": "stopped"}

    def status(self) -> dict:
        """Return current state without side effects."""
        if self._engine is None:
            return {"status": "stopped", "connected": False}
        return {
            "status": "connected",
            "connected": True,
            "started_at": self._started_at,
            "uptime_s": round(time.time() - (self._started_at or 0), 1),
            "reticolo_path": str(self._reticolo_dir),
            "disk_safety": "vmax=inf, temp=D:\\matlab_temp",
        }

    # ------------------------------------------------------------------
    # solve
    # ------------------------------------------------------------------

    def solve_point(
        self,
        wl_um: float,
        nn_x: int,
        nn_y: int,
        textures: dict,
        profil: dict,
        config_id: str = "",
    ) -> dict:
        """Solve one wavelength with RETICOLO.

        Translates Python data to MATLAB workspace, calls res1/res2,
        extracts R/T/A, and returns a provenance-tagged dict.
        """
        if self._engine is None:
            return {"status": "error", "error": "engine not started"}

        t0 = time.time()

        try:
            self._engine.workspace["wl_py"] = float(wl_um)
            self._engine.workspace["nn_py"] = [int(nn_x), int(nn_y)]
            self._engine.workspace["textures_py"] = _textures_to_matlab(textures)
            self._engine.workspace["profil_py"] = _profil_to_matlab(profil)

            self._engine.eval(
                "[aa, ~] = res1(wl_py, [1,1], textures_py, nn_py, 0, 0, parm);",
                nargout=0,
            )
            self._engine.eval(
                "ef = res2(aa, profil_py);", nargout=0
            )
            self._engine.eval(
                "R_py = sum(ef.TEinc_top_reflected.efficiency);", nargout=0
            )
            self._engine.eval(
                "T_py = sum(ef.TEinc_top_transmitted.efficiency);", nargout=0
            )
            self._engine.eval("clear aa ef;", nargout=0)

            R = float(self._engine.workspace["R_py"])
            T = float(self._engine.workspace["T_py"])
            A = 1.0 - R - T
            dt = round(time.time() - t0, 3)

            return {
                "status": "ok",
                "wl_um": wl_um,
                "nn_x": nn_x,
                "nn_y": nn_y,
                "R": R,
                "T": T,
                "A": A,
                "energy_sum": R + T + A,
                "solve_time_s": dt,
                "config_id": config_id,
            }
        except Exception as exc:
            return {
                "status": "error",
                "wl_um": wl_um,
                "nn_x": nn_x,
                "nn_y": nn_y,
                "error": str(exc),
                "config_id": config_id,
            }


# ------------------------------------------------------------------
# MATLAB data translation helpers
# ------------------------------------------------------------------

def _textures_to_matlab(textures: dict) -> list:
    """Convert a Python dict/list textures to a MATLAB cell."""
    if isinstance(textures, dict):
        items = [textures[str(i)] if str(i) in textures
                 else textures.get(i, 1)
                 for i in range(len(textures))]
        result = []
        for item in items:
            if isinstance(item, (int, float, complex)):
                result.append(float(item) if not isinstance(item, complex)
                             else complex(item))
            elif isinstance(item, (list, tuple)):
                result.append([float(x) if not isinstance(x, complex)
                               else complex(x) for x in item])
            elif isinstance(item, dict):
                result.append(_inclusion_to_list(item))
            else:
                result.append(item)
        return result
    return list(textures)


def _inclusion_to_list(inclusion: dict) -> list:
    """Convert an inclusion dict to the RETICOLO vector format."""
    keys = ["cx", "cy", "dx", "dy", "n", "k"]
    return [float(inclusion.get(k, 0)) for k in keys]


def _profil_to_matlab(profil: dict) -> dict:
    """Convert a profil dict to RETICOLO format."""
    heights = profil.get("heights", [])
    indices = profil.get("indices", list(range(1, len(heights) + 1)))
    return [list(heights), list(indices)]

"""MATLAB Engine wrapper for RETICOLO V10.

Manages the lifecycle of one MATLAB engine session, enforces the M0 disk-safety
contract, and translates Python data to RETICOLO MATLAB calls.

RETICOLO conventions enforced here:

  textures: list of layer materials (MATLAB cell array).
    - A number n means uniform refractive index.
    - A list [bg_n, inc1, inc2, ...] means a patterned layer where each
      inclusion is [cx, cy, full_dx, full_dy, n, slice_count].
      slice_count=1 for rectangle, >1 for ellipse approximation.

  profil: {"heights": [...], "indices": [...]}.
    - heights[i] are z-positions of interfaces (um), typically [0, h1, ..., 0].
    - indices[i] are 1-based texture references; indices[0] is the semi-infinite
      superstrate above height[0]; the final height=0 marks the substrate.

  D: lattice period(s) in um — scalar for square, [Px, Py] for rectangular.

  nn: Fourier truncation orders [nx, ny].

  polarization: parm.sym.pol =  1 → TE (electric field along y/rdir1)
                               -1 → TM (magnetic field along y/rdir1).
    Both polarizations read efficiencies from ef.TEinc_top_* — the 'TEinc'
    prefix refers to the top-incident direction, not the polarization state.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .config import (
    MATLAB_TEMP_DIR,
    MAX_CONFIG_ID_LEN,
    MAX_ERROR_CHARS,
    RETICOLO_SCRATCH_DIR,
)
from .lease import (
    lease_acquire,
    lease_release,
    lease_status as _lease_status,
)


def _ensure_matlab() -> Any:
    """Lazily import the matlab package. Returns the module or raises ImportError.

    This is intentionally NOT a module-level import: matlab.engine may not be
    installed yet, and even when it is, we want import safety so unit tests
    can load engine.py without MATLAB present.
    """
    import matlab
    return matlab


class REticoloEngine:
    """Owns exactly one MATLAB engine for RETICOLO RCWA computation."""

    def __init__(self, reticolo_dir: Path) -> None:
        self._reticolo_dir = reticolo_dir
        self._engine: Any = None
        self._started_at: float | None = None
        self._matlab_temp: str = str(MATLAB_TEMP_DIR)
        self._scratch_dir: str = str(RETICOLO_SCRATCH_DIR)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> dict[str, Any]:
        """Start MATLAB engine, add RETICOLO path, apply M0 disk safety."""
        if self._engine is not None:
            return self.status()

        ls = _lease_status()
        if ls["collision"]:
            return {"status": "error", "error_code": "lease_collision",
                    "blockers": ls["blockers"]}

        import_err = _check_matlab_engine()
        if import_err:
            return {"status": "error", "error_code": "matlab_engine_not_installed",
                    "detail": import_err}

        if not self._reticolo_dir.is_dir():
            return {"status": "error", "error_code": "reticolo_dir_missing",
                    "detail": str(self._reticolo_dir)}

        acquired = lease_acquire("interactive")
        if not acquired["acquired"]:
            return {"status": "error", "error_code": "lease_acquire_failed",
                    "detail": acquired}

        try:
            import matlab.engine
            self._engine = matlab.engine.start_matlab()
            self._started_at = time.time()

            self._engine.addpath(str(self._reticolo_dir), nargout=0)
            self._engine.eval(
                f"cd('{self._scratch_dir}');", nargout=0)
            self._engine.eval("[~, ~] = retio([], inf*1i);", nargout=0)

            for var in ("TMP", "TEMP", "TMPDIR"):
                self._engine.eval(
                    f"setenv('{var}','{self._matlab_temp}');", nargout=0)

            self._engine.eval("parm = res0;", nargout=0)
            self._engine.eval("parm.res1.champ = 0;", nargout=0)
            self._engine.eval("parm.res1.trace = 0;", nargout=0)
            self._engine.eval("ro = 0; delta0 = 0;", nargout=0)

            return self.status()
        except Exception:
            self._engine = None
            self._started_at = None
            lease_release()
            raise

    def stop(self) -> dict[str, Any]:
        """Stop the MATLAB engine and clean up scratch files."""
        if self._engine is None:
            lease_release()
            return {"status": "stopped"}

        for cmd in ("retio;", "clear all;"):
            try:
                self._engine.eval(cmd, nargout=0)
            except Exception:
                pass

        try:
            self._engine.quit()
        except Exception:
            pass

        self._engine = None
        self._started_at = None
        lease_release()
        return {"status": "stopped"}

    def status(self) -> dict[str, Any]:
        """Return current state without side effects."""
        ls = _lease_status()
        if self._engine is None:
            return {"status": "stopped", "connected": False,
                    "reticolo_path": str(self._reticolo_dir),
                    "lease": ls}
        return {
            "status": "connected",
            "connected": True,
            "started_at": self._started_at,
            "uptime_s": round(time.time() - (self._started_at or 0), 1),
            "reticolo_path": str(self._reticolo_dir),
            "scratch_dir": self._scratch_dir,
            "disk_safety": "vmax=inf",
            "lease": ls,
        }

    # ------------------------------------------------------------------
    # solve
    # ------------------------------------------------------------------

    def solve_point(
        self,
        *,
        wl_um: float,
        D: float | list[float],
        nn: list[int],
        textures: list[Any],
        profil: dict[str, list],
        polarization: int = 1,
        config_id: str = "",
    ) -> dict[str, Any]:
        """Solve one wavelength with RETICOLO.

        Args:
            wl_um: Wavelength in microns.
            D: Lattice period(s) — scalar for square, [Px, Py] for rectangular.
            nn: Fourier truncation orders [nx, ny].
            textures: RETICOLO texture cell array (Python list).
            profil: {"heights": [z0, z1, ..., 0], "indices": [i0, i1, ...]}.
            polarization: 1 for TE, -1 for TM (parm.sym.pol).
            config_id: Provenance tag.

        Returns:
            {status, wl_um, nn, R, T, A_balance, passive, solve_time_s, config_id}
        """
        if self._engine is None:
            return {"status": "error", "error_code": "engine_not_started",
                    "config_id": config_id}

        D_list = [float(D)] if isinstance(D, (int, float)) else [float(v) for v in D]
        if len(D_list) not in (1, 2):
            return {"status": "error", "error_code": "invalid_D",
                    "detail": "D must be scalar or [Px, Py]",
                    "config_id": config_id}

        nn_int = [int(nn[0]), int(nn[1])]
        pol = int(polarization)
        if pol not in (-1, 1):
            return {"status": "error", "error_code": "invalid_polarization",
                    "detail": "polarization must be 1 (TE) or -1 (TM)",
                    "config_id": config_id}

        matlab = _ensure_matlab()
        t0 = time.time()

        try:
            eng = self._engine

            eng.workspace["_wl"] = float(wl_um)
            eng.workspace["_D"] = matlab.double(D_list)
            eng.workspace["_nn"] = matlab.int32([nn_int])
            eng.workspace["_textures"] = _textures_to_cell(eng, matlab, textures)
            eng.workspace["_heights"] = matlab.double(
                [float(v) for v in profil["heights"]])
            eng.workspace["_pindices"] = matlab.int32(
                [[int(v) for v in profil["indices"]]])

            # TE (pol=1) and TM (pol=-1) both read from ef.TEinc_top_*
            # — the prefix refers to top-incident direction, not polarization.
            eng.eval(f"parm.sym.pol = {pol};", nargout=0)

            eng.eval(
                "[_aa, ~] = res1(_wl, _D, _textures, _nn, ro, delta0, parm);",
                nargout=0)
            eng.eval(
                "ef = res2(_aa, {_heights, _pindices});", nargout=0)
            eng.eval(
                "_R = sum(ef.TEinc_top_reflected.efficiency);", nargout=0)
            eng.eval(
                "_T = sum(ef.TEinc_top_transmitted.efficiency);", nargout=0)
            eng.eval("clear _aa ef;", nargout=0)

            R = float(eng.workspace["_R"])
            T = float(eng.workspace["_T"])
            A_balance = 1.0 - R - T
            dt = round(time.time() - t0, 3)
            passive = bool(0 <= R <= 1 and 0 <= T <= 1 and 0 <= A_balance <= 1)

            return {
                "status": "ok",
                "wl_um": wl_um,
                "nn": nn_int,
                "polarization": pol,
                "R": R,
                "T": T,
                "A_balance": A_balance,
                "passive": passive,
                "solve_time_s": dt,
                "config_id": config_id,
            }
        except Exception as exc:
            return {
                "status": "error",
                "error_code": "solve_failed",
                "wl_um": wl_um,
                "nn": nn_int,
                "polarization": pol,
                "error": _classify_error(exc),
                "config_id": config_id,
            }


# ------------------------------------------------------------------
# MATLAB cell-array helpers
# ------------------------------------------------------------------

def _textures_to_cell(eng: Any, matlab: Any, textures: list[Any]) -> Any:
    """Build a MATLAB cell array from Python textures list.

    Each entry:
      - number → complex scalar (refractive index n + i*k).
      - list starting with a number → cell array {bg_n, inc1, inc2, ...}.
    """
    cell = eng.cell(1, len(textures))
    for i, tex in enumerate(textures):
        if isinstance(tex, (int, float, complex)):
            cell[i] = complex(tex)
        elif isinstance(tex, (list, tuple)):
            if tex and isinstance(tex[0], (int, float, complex)):
                sub = eng.cell(1, len(tex))
                for j, item in enumerate(tex):
                    if isinstance(item, (list, tuple)):
                        sub[j] = matlab.double([float(x) for x in item])
                    else:
                        sub[j] = complex(item)
                cell[i] = sub
            else:
                cell[i] = matlab.double([float(x) for x in tex])
        else:
            cell[i] = complex(tex)
    return cell


def _check_matlab_engine() -> str:
    """Return empty string if matlab.engine is importable, else error text."""
    try:
        import matlab.engine  # noqa: F401
        return ""
    except ImportError:
        return ("matlab.engine not installed. "
                "From the repo root, run: "
                "pip install \"D:\\Program Files\\MATLAB\\R2025b\\"
                "extern\\engines\\python\"")


def _classify_error(exc: Exception) -> str:
    """Return a bounded error string, classified by type."""
    msg = str(exc)
    if len(msg) > MAX_ERROR_CHARS:
        msg = msg[:MAX_ERROR_CHARS - 3] + "..."
    if "disk" in msg.lower() or "space" in msg.lower():
        return f"disk_error: {msg}"
    if "memory" in msg.lower() or "out of memory" in msg.lower():
        return f"memory_error: {msg}"
    if "undefined" in msg.lower():
        return f"matlab_undefined: {msg}"
    return msg

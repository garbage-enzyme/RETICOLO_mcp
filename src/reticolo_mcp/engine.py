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
    Result branches are ef.TEinc_top_* for TE and ef.TMinc_top_* for TM.
"""

from __future__ import annotations

import csv
import locale
import math
import os
import subprocess
import threading
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
    HEARTBEAT_INTERVAL_S,
    lease_acquire,
    lease_heartbeat,
    lease_release,
    lease_status as _lease_status,
    _process_creation_date,
)

# MATLAB R2025b can keep MATLAB.exe alive for more than five seconds after
# Engine.quit() returns on Windows. Keep cleanup bounded, but allow the observed
# asynchronous shutdown to finish before retaining fail-closed ownership evidence.
PROCESS_EXIT_WAIT_S = 30.0


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
        self._lease_token: str = ""
        self._lease_healthy: bool = True
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._mode: str = "memory"
        self._matlab_processes: dict[int, float] = {}
        self._quit_requested: bool = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(
        self, mode: str = "memory", label: str = "interactive",
    ) -> dict[str, Any]:
        """Start MATLAB engine, add RETICOLO path, apply M0 disk safety.

        Args:
            mode: "memory" (no disk spill, vmax=inf) or "scratch"
                  (per-point cleanup via retio in owned directory).
        """
        if self._engine is not None:
            return self.status()

        if mode not in ("memory", "scratch"):
            return {"status": "error", "error_code": "invalid_mode",
                    "detail": "mode must be 'memory' or 'scratch'"}
        self._mode = mode

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

        inventory_before = _matlab_process_inventory()
        if inventory_before is None:
            return {
                "status": "error",
                "error_code": "matlab_process_inventory_unavailable",
            }
        if inventory_before:
            return {
                "status": "error",
                "error_code": "matlab_process_collision",
                "matlab_pids": sorted(inventory_before),
            }

        acquired = lease_acquire(label, mode=mode)
        if not acquired["acquired"]:
            return {"status": "error", "error_code": "lease_acquire_failed",
                    "detail": acquired}
        self._lease_token = acquired.get("token", "")
        self._lease_healthy = True
        self._start_heartbeat()

        # P0-7: create dirs and set env before engine start. Restore the MCP
        # host environment after MATLAB has inherited and confirmed its copy.
        Path(self._matlab_temp).mkdir(parents=True, exist_ok=True)
        Path(self._scratch_dir).mkdir(parents=True, exist_ok=True)
        previous_temp_env = {
            var: os.environ.get(var) for var in ("TMP", "TEMP", "TMPDIR")
        }
        for var in ("TMP", "TEMP", "TMPDIR"):
            os.environ[var] = self._matlab_temp

        try:
            import matlab.engine
            self._engine = matlab.engine.start_matlab()
            self._quit_requested = False
            self._started_at = time.time()
            inventory_after = _matlab_process_inventory()
            if inventory_after is None:
                raise RuntimeError("MATLAB process inventory unavailable after start")
            if len(inventory_after) != 1:
                raise RuntimeError(
                    "expected exactly one owned MATLAB process after start, "
                    f"found {len(inventory_after)}"
                )
            self._matlab_processes = inventory_after

            self._engine.addpath(str(self._reticolo_dir), nargout=0)
            self._engine.eval(
                f"cd('{self._scratch_dir}');", nargout=0)

            if mode == "memory":
                self._engine.eval("[~, ~] = retio([], inf*1i);", nargout=0)

            for var in ("TMP", "TEMP", "TMPDIR"):
                self._engine.eval(
                    f"setenv('{var}','{self._matlab_temp}');", nargout=0)

            # health check: verify RETICOLO functions are reachable
            self._engine.eval("parm = res0;", nargout=0)
            self._engine.eval("parm.res1.champ = 0;", nargout=0)
            self._engine.eval("parm.res1.trace = 0;", nargout=0)
            self._engine.eval("ro = 0; delta0 = 0;", nargout=0)

            return self.status()
        except Exception as exc:
            owned_engine = self._engine
            if owned_engine is not None:
                try:
                    owned_engine.quit()
                    self._quit_requested = True
                except Exception as cleanup_exc:
                    return {
                        "status": "cleanup_uncertain",
                        "connected": True,
                        "error_code": "startup_matlab_quit_failed",
                        "detail": _classify_error(exc),
                        "cleanup_error": _classify_error(cleanup_exc),
                    }
                if not self._wait_for_matlab_absent():
                    return {
                        "status": "cleanup_uncertain",
                        "connected": False,
                        "error_code": "startup_matlab_process_cleanup_unproven",
                        "detail": _classify_error(exc),
                        "matlab_pids": sorted(self._matlab_processes),
                    }
            else:
                inventory_after_failure = _matlab_process_inventory()
                if inventory_after_failure is None or inventory_after_failure:
                    self._matlab_processes = inventory_after_failure or {}
                    return {
                        "status": "cleanup_uncertain",
                        "connected": False,
                        "error_code": "startup_matlab_process_cleanup_unproven",
                        "detail": _classify_error(exc),
                        "matlab_pids": sorted(self._matlab_processes),
                    }
            self._engine = None
            self._started_at = None
            self._matlab_processes = {}
            self._quit_requested = False
            release = self._release_owned_lease()
            if not release["released"]:
                return {
                    "status": "cleanup_uncertain",
                    "connected": False,
                    "error_code": "startup_lease_release_failed",
                    "detail": _classify_error(exc),
                    "cleanup_error": release.get("detail", "lease release failed"),
                }
            return {
                "status": "error",
                "connected": False,
                "error_code": "engine_start_failed",
                "detail": _classify_error(exc),
            }
        finally:
            for var, previous in previous_temp_env.items():
                if previous is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = previous

    def stop(self) -> dict[str, Any]:
        """Stop the MATLAB engine and clean up scratch files."""
        if self._engine is None:
            if self._matlab_processes and not self._wait_for_matlab_absent():
                return {
                    "status": "cleanup_uncertain",
                    "connected": False,
                    "error_code": "matlab_process_cleanup_unproven",
                    "matlab_pids": sorted(self._matlab_processes),
                }
            self._matlab_processes = {}
            release = self._release_owned_lease()
            if not release["released"]:
                return {
                    "status": "cleanup_uncertain",
                    "connected": False,
                    "error_code": "lease_release_failed",
                    "detail": release.get("detail", "lease release failed"),
                }
            return {"status": "stopped"}

        if self._quit_requested:
            if not self._wait_for_matlab_absent():
                return {
                    "status": "cleanup_uncertain",
                    "connected": False,
                    "error_code": "matlab_process_cleanup_unproven",
                    "matlab_pids": sorted(self._matlab_processes),
                }
            return self._finalize_stopped(recovered_async_exit=True)

        cleanup_warnings = []
        for cmd in ("retio;", "clear all;"):
            try:
                self._engine.eval(cmd, nargout=0)
            except Exception as exc:
                cleanup_warnings.append(_classify_error(exc))

        try:
            self._engine.quit()
            self._quit_requested = True
        except Exception as exc:
            return {
                "status": "cleanup_uncertain",
                "connected": True,
                "error_code": "matlab_quit_failed",
                "detail": _classify_error(exc),
                "cleanup_warnings": cleanup_warnings,
            }

        if not self._wait_for_matlab_absent():
            return {
                "status": "cleanup_uncertain",
                "connected": False,
                "error_code": "matlab_process_cleanup_unproven",
                "matlab_pids": sorted(self._matlab_processes),
                "cleanup_warnings": cleanup_warnings,
            }

        return self._finalize_stopped(cleanup_warnings=cleanup_warnings)

    def _finalize_stopped(
        self,
        *,
        cleanup_warnings: list[str] | None = None,
        recovered_async_exit: bool = False,
    ) -> dict[str, Any]:
        """Clear a proven-absent MATLAB owner and release its exact lease."""
        warnings = cleanup_warnings or []
        self._engine = None
        self._started_at = None
        self._matlab_processes = {}
        self._quit_requested = False
        release = self._release_owned_lease()
        if not release["released"]:
            return {
                "status": "cleanup_uncertain",
                "connected": False,
                "error_code": "lease_release_failed",
                "detail": release.get("detail", "lease release failed"),
                "cleanup_warnings": warnings,
            }
        result: dict[str, Any] = {"status": "stopped"}
        if warnings:
            result["cleanup_warnings"] = warnings
        if recovered_async_exit:
            result["recovered_async_exit"] = True
        return result

    def _release_owned_lease(self) -> dict[str, Any]:
        """Release only the exact owned lease; retain heartbeat on uncertainty."""
        token = self._lease_token
        if not token:
            self._stop_heartbeat()
            return {"released": True, "detail": "no owned lease"}
        self._stop_heartbeat()
        try:
            release = lease_release(token)
        except Exception as exc:
            release = {"released": False, "detail": _classify_error(exc)}
        if release.get("released"):
            self._lease_token = ""
            return release
        self._start_heartbeat()
        return release

    def _wait_for_matlab_absent(self) -> bool:
        """Boundedly prove that no MATLAB process remains after owned quit."""
        if not self._matlab_processes:
            return True
        deadline = time.monotonic() + PROCESS_EXIT_WAIT_S
        while True:
            inventory = _matlab_process_inventory()
            if inventory is None:
                return False
            if not inventory:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    def status(self) -> dict[str, Any]:
        """Return current state without side effects."""
        ls = _lease_status()
        if self._engine is None:
            return {"status": "stopped", "connected": False,
                    "reticolo_path": str(self._reticolo_dir),
                    "mode": self._mode,
                    "lease": ls}
        return {
            "status": "connected",
            "connected": True,
            "started_at": self._started_at,
            "uptime_s": round(time.time() - (self._started_at or 0), 1),
            "reticolo_path": str(self._reticolo_dir),
            "scratch_dir": self._scratch_dir,
            "mode": self._mode,
            "matlab_processes": [
                {"pid": pid, "creation_date": creation_date}
                for pid, creation_date in sorted(self._matlab_processes.items())
            ],
            "lease_heartbeat_healthy": self._lease_healthy,
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
        theta_deg: float = 0.0,
        azimuth_deg: float = 0.0,
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
            theta_deg: Signed incidence elevation in degrees (-90, 90).
            azimuth_deg: Incidence-plane azimuth in degrees [-360, 360].
            config_id: Provenance tag.

        Returns:
            {status, wl_um, nn, R, T, A_balance, passive, solve_time_s, config_id}
        """
        if self._engine is None:
            return {"status": "error", "error_code": "engine_not_started",
                    "config_id": config_id}
        if not self._lease_healthy:
            return {"status": "error", "error_code": "solver_lease_lost",
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
        if (
            isinstance(theta_deg, bool)
            or not isinstance(theta_deg, (int, float))
            or not math.isfinite(float(theta_deg))
            or not -90.0 < float(theta_deg) < 90.0
        ):
            return {
                "status": "error", "error_code": "invalid_theta",
                "detail": "theta_deg must be finite and strictly between -90 and 90",
                "config_id": config_id,
            }
        if (
            isinstance(azimuth_deg, bool)
            or not isinstance(azimuth_deg, (int, float))
            or not math.isfinite(float(azimuth_deg))
            or not -360.0 <= float(azimuth_deg) <= 360.0
        ):
            return {
                "status": "error", "error_code": "invalid_azimuth",
                "detail": "azimuth_deg must be finite and in [-360, 360]",
                "config_id": config_id,
            }

        matlab = _ensure_matlab()
        t0 = time.time()

        try:
            eng = self._engine

            eng.workspace["py_wl"] = float(wl_um)
            eng.workspace["py_D"] = matlab.double(D_list)
            eng.workspace["py_nn"] = matlab.double([nn_int])
            eng.workspace["py_textures"] = _textures_to_cell(eng, matlab, textures)
            eng.workspace["py_heights"] = matlab.double(
                [float(v) for v in profil["heights"]])
            eng.workspace["py_indices"] = matlab.int32(
                [[int(v) for v in profil["indices"]]])
            eng.workspace["py_theta_deg"] = float(theta_deg)
            eng.workspace["py_azimuth_deg"] = float(azimuth_deg)

            eng.eval(f"parm.sym.pol = {pol};", nargout=0)
            eng.eval(
                "ro = py_theta_deg*pi/180; "
                "delta0 = py_azimuth_deg*pi/180;",
                nargout=0,
            )

            eng.eval(
                "[py_aa, ~] = res1(py_wl, py_D, py_textures, py_nn, ro, delta0, parm);",
                nargout=0)
            eng.eval(
                "ef = res2(py_aa, {py_heights, py_indices});", nargout=0)
            channel = "TEinc" if pol == 1 else "TMinc"
            eng.eval(
                f"py_R = sum(ef.{channel}_top_reflected.efficiency);", nargout=0)
            eng.eval(
                f"py_T = sum(ef.{channel}_top_transmitted.efficiency);", nargout=0)
            eng.eval("clear py_aa ef;", nargout=0)

            R = float(eng.workspace["py_R"])
            T = float(eng.workspace["py_T"])
            if not self._lease_healthy:
                return {
                    "status": "error",
                    "error_code": "solver_lease_lost",
                    "wl_um": wl_um,
                    "nn": nn_int,
                    "polarization": pol,
                    "config_id": config_id,
                }
            A_balance = 1.0 - R - T
            dt = round(time.time() - t0, 3)
            passive = bool(0 <= R <= 1 and 0 <= T <= 1 and 0 <= A_balance <= 1)

            return {
                "status": "ok",
                "wl_um": wl_um,
                "nn": nn_int,
                "polarization": pol,
                "theta_deg": float(theta_deg),
                "azimuth_deg": float(azimuth_deg),
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
                "theta_deg": float(theta_deg),
                "azimuth_deg": float(azimuth_deg),
                "error": _classify_error(exc),
                "config_id": config_id,
            }

    def _start_heartbeat(self) -> None:
        """Keep lease ownership fresh while MATLAB calls block this thread."""
        self._stop_heartbeat()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="reticolo-lease-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._heartbeat_thread = None

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(HEARTBEAT_INTERVAL_S):
            token = self._lease_token
            if not token or not lease_heartbeat(token):
                self._lease_healthy = False
                return


# ------------------------------------------------------------------
# MATLAB cell-array helpers
# ------------------------------------------------------------------

def _textures_to_cell(eng: Any, matlab: Any, textures: list[Any]) -> Any:
    """Build a MATLAB cell array from Python textures list.

    Each entry:
      - number → complex scalar (refractive index n + i*k).
      - list starting with a number → cell array {bg_n, inc1, inc2, ...}
        where each inclusion is a complex double vector [cx,cy,dx,dy,n,k].
    """
    cell = eng.cell(1, len(textures))
    for i, tex in enumerate(textures):
        if isinstance(tex, (int, float, complex)):
            value = complex(tex)
            cell[i] = value if value.imag != 0 else float(value.real)
        elif isinstance(tex, (list, tuple)):
            if tex and isinstance(tex[0], (int, float, complex)):
                sub = eng.cell(1, len(tex))
                for j, item in enumerate(tex):
                    if isinstance(item, (list, tuple)):
                        has_complex = any(
                            isinstance(x, complex) and x.imag != 0 for x in item
                        )
                        if has_complex:
                            sub[j] = matlab.double(
                                [complex(float(x.real), float(x.imag))
                                 if isinstance(x, complex) else float(x)
                                 for x in item],
                                is_complex=True,
                            )
                        else:
                            sub[j] = matlab.double([
                                float(x.real) if isinstance(x, complex) else float(x)
                                for x in item
                            ])
                    else:
                        value = complex(item)
                        sub[j] = value if value.imag != 0 else float(value.real)
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


def _matlab_process_inventory() -> dict[int, float] | None:
    """Return exact MATLAB PID/creation evidence, or None if incomplete."""
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq MATLAB.exe", "/FO", "CSV", "/NH"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    encoding = locale.getpreferredencoding(False) or "utf-8"
    output = completed.stdout.decode(encoding, errors="replace")
    inventory: dict[int, float] = {}
    for row in csv.reader(output.splitlines()):
        if len(row) < 2 or row[0].strip().lower() != "matlab.exe":
            continue
        try:
            pid = int(row[1].replace(",", "").strip())
        except ValueError:
            return None
        creation_date = _process_creation_date(pid)
        if creation_date is None:
            return None
        inventory[pid] = creation_date
    return inventory


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

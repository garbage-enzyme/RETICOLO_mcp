"""Detached RETICOLO worker — runs staged sweeps as independent processes.

Called by the MCP server on job_submit. Owns its MATLAB engine and
solver lease. Survives MCP server restart.

Usage: python -m reticolo_mcp.worker <job_id>
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

# Ensure src/ is importable when run standalone
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from reticolo_mcp.jobs import (
    append_event,
    read_events,
    read_spec,
    read_state,
    results_path,
    write_state,
    worker_log_path,
)
from reticolo_mcp.engine import REticoloEngine
from reticolo_mcp.config import RETICOLO_DIR
from reticolo_mcp.sweep import run_sweep
from reticolo_mcp.lease import lease_acquire, lease_release, lease_heartbeat


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m reticolo_mcp.worker <job_id>", file=sys.stderr)
        return 1

    job_id = sys.argv[1]
    _setup_logging(job_id)

    spec = read_spec(job_id)
    if spec is None:
        _log(job_id, "spec not found")
        return 1

    try:
        return _run_job(job_id, spec)
    except Exception:
        traceback.print_exc()
        try:
            write_state(job_id, {
                "status": "failed", "worker_pid": os.getpid(),
                "error": traceback.format_exc()[-1000:],
            })
        except Exception:
            pass
        return 1


def _run_job(job_id: str, spec: dict[str, Any]) -> int:
    # ------------------------------------------------------------------
    # startup
    # ------------------------------------------------------------------
    _log(job_id, f"worker PID={os.getpid()} starting")
    write_state(job_id, {"status": "starting", "worker_pid": os.getpid(),
                         "attempted_at": time.time()})
    append_event(job_id, {"event": "worker_starting", "pid": os.getpid()})

    acquired = lease_acquire(f"job:{job_id}", mode=spec.get("mode", "memory"))
    if not acquired["acquired"]:
        write_state(job_id, {"status": "failed",
                             "error": f"lease: {acquired}"})
        append_event(job_id, {"event": "lease_failed", "detail": acquired})
        _log(job_id, f"lease failed: {acquired}")
        return 1

    eng = REticoloEngine(RETICOLO_DIR)
    start_r = eng.start(mode=spec.get("mode", "memory"))
    if start_r["status"] != "connected":
        write_state(job_id, {"status": "failed",
                             "error": f"engine start: {start_r}"})
        append_event(job_id, {"event": "engine_start_failed",
                              "detail": start_r})
        lease_release()
        _log(job_id, f"engine start failed: {start_r}")
        return 1

    try:
        write_state(job_id, {"status": "running", "worker_pid": os.getpid(),
                             "started_at": time.time()})
        append_event(job_id, {"event": "sweep_started"})

        csv = str(results_path(job_id))
        D = spec.get("D", [1.0])

        result = run_sweep(
            engine=eng,
            wls_um=spec["wls_um"],
            nn=spec["nn"],
            D=D,
            textures=spec.get("textures", [1.0]),
            profil={
                "heights": spec.get("profil_heights", [0, 0]),
                "indices": spec.get("profil_indices", [1, 1]),
            },
            polarization=spec.get("polarization", 1),
            config_id=spec.get("config_label", job_id),
            config_hash=spec.get("config_hash", ""),
            csv_path=csv,
            resume=True,
        )

        write_state(job_id, {
            "status": "completed",
            "worker_pid": os.getpid(),
            "completed_at": time.time(),
            "solved": result["solved"],
            "skipped": result["skipped"],
            "errors": result["errors"],
            "runtime_s": result["runtime_s"],
        })
        append_event(job_id, {
            "event": "completed",
            "solved": result["solved"],
            "skipped": result["skipped"],
            "errors": result["errors"],
            "runtime_s": result["runtime_s"],
        })
        _log(job_id, f"completed: {result['solved']} solved, "
             f"{result['skipped']} skipped, {result['errors']} errors")
        return 0 if result["errors"] == 0 else 1

    except Exception:
        write_state(job_id, {
            "status": "failed",
            "worker_pid": os.getpid(),
            "error": traceback.format_exc()[-1000:],
        })
        append_event(job_id, {"event": "worker_crashed",
                              "error": traceback.format_exc()[-500]})
        _log(job_id, f"crashed: {traceback.format_exc()[-200]}")
        return 1

    finally:
        try:
            eng.stop()
        except Exception:
            pass
        lease_release()
        append_event(job_id, {"event": "worker_exited"})
        _log(job_id, "worker exited")


def _setup_logging(job_id: str) -> None:
    log_path = worker_log_path(job_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # redirect stdout/stderr to worker log
    log_fh = open(str(log_path), "a", encoding="utf-8")
    sys.stdout = log_fh
    sys.stderr = log_fh


def _log(job_id: str, message: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[{ts}] [{job_id}] {message}", flush=True)


if __name__ == "__main__":
    sys.exit(main())

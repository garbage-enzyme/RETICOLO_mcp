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
    transition_state,
    worker_log_path,
)
from reticolo_mcp.engine import REticoloEngine
from reticolo_mcp.config import RETICOLO_DIR
from reticolo_mcp.sweep import run_sweep


def _to_complex(textures: list[Any]) -> list[Any]:
    """Convert JSON-safe [[re, im], ...] textures to Python complex numbers."""
    result = []
    for tex in textures:
        if isinstance(tex, list) and len(tex) == 2 and all(
            isinstance(x, (int, float)) for x in tex):
            result.append(complex(tex[0], tex[1]))
        elif isinstance(tex, list):
            sub = []
            for item in tex:
                if isinstance(item, list) and len(item) == 2 and all(
                    isinstance(x, (int, float)) for x in item):
                    sub.append(complex(item[0], item[1]))
                elif isinstance(item, list):
                    sub.append(item)
                else:
                    sub.append(item)
            result.append(sub)
        else:
            result.append(tex)
    return result


def main(job_id: str | None = None) -> int:
    if job_id is None:
        if len(sys.argv) < 2:
            print("Usage: python -m reticolo_mcp.worker <job_id>", file=sys.stderr)
            return 1
        job_id = sys.argv[1]
    _setup_logging(job_id)

    spec = read_spec(job_id)
    if spec is None:
        _log(job_id, "spec not found")
        return 1

    # Convert JSON-safe [re, im] pairs back to Python complex
    spec["_textures_complex"] = _to_complex(spec.get("textures", []))

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
    submitted_state = read_state(job_id) or {}
    attempt = int(submitted_state.get("attempt", 1))
    attempt_id = str(submitted_state.get("attempt_id", ""))
    _log(job_id, f"worker PID={os.getpid()} starting attempt={attempt}")
    starting = transition_state(
        job_id, allowed_from={"submitted"}, attempt_id=attempt_id,
        updates={"status": "starting", "worker_pid": os.getpid(),
                 "attempted_at": time.time()},
    )
    if not starting["updated"]:
        _log(job_id, f"startup transition refused: {starting['reason']}")
        return 1
    append_event(job_id, {"event": "worker_starting", "pid": os.getpid(),
                          "attempt": attempt, "attempt_id": attempt_id})

    eng = REticoloEngine(RETICOLO_DIR)
    start_r = eng.start(
        mode=spec.get("mode", "memory"), label=f"job:{job_id}",
    )
    if start_r["status"] != "connected":
        transition_state(
            job_id, allowed_from={"starting"}, attempt_id=attempt_id,
            updates={"status": "failed", "error": f"engine start: {start_r}"},
        )
        append_event(job_id, {"event": "engine_start_failed",
                              "detail": start_r})
        _log(job_id, f"engine start failed: {start_r}")
        return 1

    try:
        running = transition_state(
            job_id, allowed_from={"starting"}, attempt_id=attempt_id,
            updates={"status": "running", "worker_pid": os.getpid(),
                     "started_at": time.time()},
        )
        if not running["updated"]:
            if _cancel_requested(job_id, attempt_id):
                transition_state(
                    job_id, allowed_from={"cancel_requested", "cancelling"},
                    attempt_id=attempt_id,
                    updates={"status": "interrupted",
                             "reason": "cancelled_during_engine_start"},
                )
                return 0
            raise RuntimeError(f"running transition refused: {running['reason']}")
        append_event(job_id, {"event": "sweep_started"})

        csv = str(results_path(job_id))
        D = spec.get("D", [1.0])

        result = run_sweep(
            engine=eng,
            wls_um=spec["wls_um"],
            nn=spec["nn"],
            D=D,
            textures=spec.get("_textures_complex", spec.get("textures", [1.0])),
            profil={
                "heights": spec.get("profil_heights", [0, 0]),
                "indices": spec.get("profil_indices", [1, 1]),
            },
            polarization=spec.get("polarization", 1),
            config_id=spec.get("config_label", job_id),
            config_hash=spec.get("config_hash", ""),
            csv_path=csv,
            resume=True,
            should_cancel=lambda: _cancel_requested(job_id, attempt_id),
        )

        if result.get("cancel_observed"):
            transition_state(
                job_id,
                allowed_from={"running", "cancel_requested", "cancelling"},
                attempt_id=attempt_id,
                updates={
                "status": "interrupted",
                "worker_pid": os.getpid(),
                "interrupted_at": time.time(),
                "reason": "cooperative_cancel_boundary",
                "solved": result["solved"],
                "skipped": result["skipped"],
                "errors": result["errors"],
                "runtime_s": result["runtime_s"],
            })
            append_event(job_id, {
                "event": "cancel_observed_at_safe_boundary",
                "solved": result["solved"],
                "skipped": result["skipped"],
                "errors": result["errors"],
            })
            _log(job_id, "cancellation observed at a safe point boundary")
            return 0

        terminal = transition_state(job_id, allowed_from={"running"},
            attempt_id=attempt_id, updates={
            "status": (
                "completed" if result["errors"] == 0
                else "completed_with_errors"
            ),
            "worker_pid": os.getpid(),
            "completed_at": time.time(),
            "solved": result["solved"],
            "skipped": result["skipped"],
            "errors": result["errors"],
            "runtime_s": result["runtime_s"],
        })
        if not terminal["updated"]:
            if _cancel_requested(job_id, attempt_id):
                transition_state(
                    job_id, allowed_from={"cancel_requested", "cancelling"},
                    attempt_id=attempt_id,
                    updates={"status": "interrupted",
                             "reason": "cancelled_before_terminal_commit"},
                )
                return 0
            raise RuntimeError(f"terminal transition refused: {terminal['reason']}")
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
        transition_state(
            job_id,
            allowed_from={"starting", "running", "cancel_requested", "cancelling"},
            attempt_id=attempt_id,
            updates={"status": "failed",
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
        append_event(job_id, {"event": "worker_exited"})
        _log(job_id, "worker exited")


def _cancel_requested(job_id: str, attempt_id: str = "") -> bool:
    """Return True only for the active job's durable cancel request."""
    state = read_state(job_id)
    if not state or state.get("status") not in {"cancel_requested", "cancelling"}:
        return False
    return not attempt_id or state.get("attempt_id") == attempt_id


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

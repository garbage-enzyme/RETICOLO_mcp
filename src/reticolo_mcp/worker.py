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
from reticolo_mcp.resources import ResourcePolicy, evaluate_admission, sample_resources

MAX_WORKER_LOG_BYTES = 4 * 1024 * 1024


def _to_complex(textures: list[Any]) -> list[Any]:
    """Convert JSON-safe [[re, im], ...] textures to Python complex numbers."""
    result: list[Any] = []
    for tex in textures:
        if isinstance(tex, (int, float, complex)) and not isinstance(tex, bool):
            result.append(tex)
            continue
        if _is_pair(tex):
            result.append(complex(tex[0], tex[1]))
            continue
        if not isinstance(tex, list) or not tex:
            raise ValueError("invalid normalized texture")
        background = complex(tex[0][0], tex[0][1]) if _is_pair(tex[0]) else tex[0]
        patterned: list[Any] = [background]
        for inclusion in tex[1:]:
            if not isinstance(inclusion, list) or len(inclusion) not in (6, 7):
                raise ValueError("invalid normalized inclusion")
            if len(inclusion) == 7:
                converted = [
                    *inclusion[:4], complex(inclusion[4], inclusion[5]), inclusion[6],
                ]
            else:
                converted = list(inclusion)
            if _is_pair(converted[4]):
                converted[4] = complex(converted[4][0], converted[4][1])
            patterned.append(converted)
        result.append(patterned)
    return result


def _is_pair(value: Any) -> bool:
    return (
        isinstance(value, list) and len(value) == 2
        and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value)
    )


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
        _finalize_cleanup(job_id, attempt_id, eng)
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
        job_started_monotonic = time.monotonic()

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
            before_point=lambda wl: _admit_point(
                job_id, attempt_id, spec, wl, job_started_monotonic,
            ),
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

        if result.get("status") == "resource_refused":
            transition_state(
                job_id, allowed_from={"running"}, attempt_id=attempt_id,
                updates={
                    "status": "resource_refused",
                    "resource_decision": result.get("resource_decision"),
                    "solved": result["solved"], "skipped": result["skipped"],
                    "errors": result["errors"],
                },
            )
            append_event(job_id, {
                "event": "resource_refused_before_point",
                "decision": result.get("resource_decision"),
            })
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
        if not _finalize_cleanup(job_id, attempt_id, eng):
            return 1


def _finalize_cleanup(
    job_id: str, attempt_id: str, eng: REticoloEngine,
) -> bool:
    """Record a terminal exit only after exact engine cleanup is proven."""
    try:
        cleanup = eng.stop()
    except Exception as exc:
        cleanup = {
            "status": "cleanup_uncertain",
            "error_code": "engine_stop_raised",
            "detail": f"{type(exc).__name__}: {exc}"[:500],
        }
    if cleanup.get("status") != "stopped":
        evidence = {
            key: cleanup[key]
            for key in ("status", "error_code", "detail", "connected")
            if key in cleanup
        }
        current = read_state(job_id) or {}
        current_status = current.get("status")
        updated = transition_state(
            job_id,
            allowed_from={current_status} if current_status else set(),
            attempt_id=attempt_id,
            updates={
                "status": "cleanup_uncertain",
                "cleanup": evidence,
                "cleanup_checked_at": time.time(),
            },
        )
        try:
            append_event(job_id, {
                "event": "worker_cleanup_uncertain",
                "attempt_id": attempt_id,
                "cleanup": evidence,
                "state_updated": bool(updated.get("updated")),
            })
        except Exception:
            pass
        _log(job_id, f"cleanup uncertain: {evidence}")
        return False
    append_event(job_id, {
        "event": "worker_exited",
        "attempt_id": attempt_id,
        "cleanup_proven": True,
    })
    _log(job_id, "worker exited after proven cleanup")
    return True


def _cancel_requested(job_id: str, attempt_id: str = "") -> bool:
    """Return True only for the active job's durable cancel request."""
    state = read_state(job_id)
    if not state or state.get("status") not in {"cancel_requested", "cancelling"}:
        return False
    return not attempt_id or state.get("attempt_id") == attempt_id


def _admit_point(
    job_id: str, attempt_id: str, spec: dict[str, Any], wl: float,
    started_monotonic: float,
) -> dict[str, Any]:
    policy = ResourcePolicy.model_validate(spec.get("resource_policy"))
    remaining_wall = max(
        0.0, policy.wall_budget_s - (time.monotonic() - started_monotonic),
    )
    snapshot = sample_resources(remaining_wall_s=remaining_wall)
    decision = evaluate_admission(policy, snapshot, point_count=1)
    append_event(job_id, {
        "event": "pre_point_resource_admission", "attempt_id": attempt_id,
        "wl_um": wl, "decision": decision,
    })
    return decision


def _setup_logging(job_id: str) -> None:
    log_path = worker_log_path(job_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # redirect stdout/stderr to worker log
    log_fh = open(str(log_path), "ab")
    bounded = _BoundedLogWriter(log_fh, MAX_WORKER_LOG_BYTES)
    sys.stdout = bounded
    sys.stderr = bounded


def _log(job_id: str, message: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[{ts}] [{job_id}] {message}", flush=True)


class _BoundedLogWriter:
    """Text writer that never lets one worker log exceed its byte budget."""

    def __init__(self, raw: Any, max_bytes: int) -> None:
        self.raw = raw
        self.max_bytes = max_bytes
        self.truncated = False

    def write(self, text: str) -> int:
        encoded = text.encode("utf-8", errors="replace")
        remaining = max(0, self.max_bytes - self.raw.tell())
        if remaining <= 0:
            self.truncated = True
            return len(text)
        chunk = encoded[:remaining]
        while chunk:
            try:
                chunk.decode("utf-8")
                break
            except UnicodeDecodeError:
                chunk = chunk[:-1]
        self.raw.write(chunk)
        if len(chunk) < len(encoded):
            self.truncated = True
        return len(text)

    def flush(self) -> None:
        self.raw.flush()
        os.fsync(self.raw.fileno())

    def isatty(self) -> bool:
        return False


if __name__ == "__main__":
    sys.exit(main())

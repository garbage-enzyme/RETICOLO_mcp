"""Lightweight solver lease for RETICOLO MCP.

Ensures only one MATLAB/RETICOLO solver owns the machine at a time.
Also checks for an active COMSOL MCP lease to prevent overlap.

Atomicity: Windows named mutex protects check-and-write. PID + creation time
prevents stale lease reuse. Heartbeat updates every 30 s by the owner.

Format (JSON, atomic write via temp+replace):
{
  "schema": "1",
  "owner": "reticolo-mcp",
  "pid": 12345,
  "created_at": 1752000000.0,
  "creation_date": 1752000000.0,
  "token": "uuid",
  "label": "job:<id>" or "interactive",
  "heartbeat": 1752000030.0,
  "mode": "memory" or "scratch"
}
"""

from __future__ import annotations

import ctypes
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .config import LEASE_PATH, RUNTIME_DIR

LEASE_SCHEMA = "1"
COMSOL_LEASE_NAME = "solver_owner.json"
HEARTBEAT_INTERVAL_S = 30
STALE_HEARTBEAT_S = 90
_MUTEX_NAME = r"Global\reticolo_mcp_lease"


def _process_creation_date(pid: int) -> float | None:
    """Return process creation time as epoch seconds, or None if dead."""
    if pid <= 0:
        return None
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0400, False, pid)
    if not handle:
        return None
    try:
        ft_create = ctypes.c_ulonglong()
        ft_exit = ctypes.c_ulonglong()
        ft_kernel = ctypes.c_ulonglong()
        ft_user = ctypes.c_ulonglong()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(ft_create),
            ctypes.byref(ft_exit),
            ctypes.byref(ft_kernel),
            ctypes.byref(ft_user),
        )
        if not ok:
            return None
        return ft_create.value / 10_000_000 - 11644473600.0
    finally:
        kernel32.CloseHandle(handle)


def _is_pid_alive(pid: int) -> bool:
    """Check if a Windows process with the given PID exists."""
    if pid <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0400, False, pid)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def _read_lease(path: Path) -> dict[str, Any] | None:
    """Read a lease file. Returns None if missing, malformed, or stale."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid", 0)
    recorded_date = data.get("creation_date", 0)
    if recorded_date:
        actual_date = _process_creation_date(pid)
        if actual_date is None:
            return None
        if abs(actual_date - recorded_date) > 1.0:
            return None
    elif not _is_pid_alive(pid):
        return None
    hb = data.get("heartbeat", 0)
    if hb and time.time() - hb > STALE_HEARTBEAT_S:
        return None
    return data


def _comsol_lease_path() -> Path | None:
    """Return the COMSOL MCP lease path if it exists in the shared runtime."""
    for candidate in (RUNTIME_DIR / COMSOL_LEASE_NAME,
                      Path("D:\\comsol_runtime") / COMSOL_LEASE_NAME):
        if candidate.exists():
            return candidate
    return None


def lease_status() -> dict[str, Any]:
    """Report current lease state. Read-only, no side effects."""
    our = _read_lease(LEASE_PATH)
    our_pid = os.getpid()

    comsol_path = _comsol_lease_path()
    comsol = _read_lease(comsol_path) if comsol_path else None

    collision = False
    blockers = []
    if comsol:
        collision = True
        blockers.append({
            "owner": comsol.get("owner", "unknown"),
            "lease": str(comsol_path),
            "pid": comsol.get("pid"),
        })
    if our and our.get("pid") != our_pid:
        collision = True
        blockers.append({
            "owner": our.get("owner", "reticolo-mcp"),
            "lease": str(LEASE_PATH),
            "pid": our.get("pid"),
        })

    return {
        "reticolo_lease": {"active": our is not None and our.get("pid") == our_pid,
                           "path": str(LEASE_PATH)},
        "comsol_lease": {"active": comsol is not None,
                         "path": str(comsol_path) if comsol_path else None},
        "collision": collision,
        "blockers": blockers,
        "ready": not collision,
    }


def lease_acquire(label: str = "interactive", mode: str = "memory") -> dict[str, Any]:
    """Acquire the solver lease atomically via named mutex.

    Args:
        label: Human-readable label, e.g. "job:<id>" or "interactive".
        mode: "memory" or "scratch".

    Returns:
        {acquired: bool, token: str, ...}
    """
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not mutex:
        return {"acquired": False, "detail": "cannot create mutex"}
    try:
        wait_result = kernel32.WaitForSingleObject(mutex, 5000)
        if wait_result != 0:
            return {"acquired": False, "detail": "mutex timeout"}

        status = lease_status()
        if status["collision"]:
            return {"acquired": False, "collision": True,
                    "blockers": status["blockers"]}

        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        pid = os.getpid()
        now = time.time()
        creation_date = _process_creation_date(pid) or now

        data = {
            "schema": LEASE_SCHEMA,
            "owner": "reticolo-mcp",
            "pid": pid,
            "created_at": now,
            "creation_date": creation_date,
            "token": token,
            "label": label,
            "heartbeat": now,
            "mode": mode,
        }

        tmp = LEASE_PATH.with_name(
            f".{LEASE_PATH.name}.{pid}.{token[:8]}.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, LEASE_PATH)

        return {"acquired": True, "token": token,
                "lease_path": str(LEASE_PATH), "pid": pid}
    finally:
        kernel32.ReleaseMutex(mutex)
        kernel32.CloseHandle(mutex)


def lease_heartbeat(token: str) -> bool:
    """Update the heartbeat timestamp. Returns True if we still own the lease."""
    our = _read_lease(LEASE_PATH)
    if our is None:
        return False
    if our.get("pid") != os.getpid():
        return False
    if our.get("token") != token:
        return False

    data = dict(our)
    data["heartbeat"] = time.time()

    tmp = LEASE_PATH.with_name(
        f".{LEASE_PATH.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, LEASE_PATH)
    return True


def lease_release(token: str | None = None) -> dict[str, Any]:
    """Release the solver lease if PID and optional owner token match."""
    our = _read_lease(LEASE_PATH)
    if our is None:
        return {"released": False, "detail": "no active lease"}
    if our.get("pid") != os.getpid():
        return {"released": False, "detail": "lease owned by another process"}
    if token is not None and our.get("token") != token:
        return {"released": False, "detail": "lease token mismatch"}
    LEASE_PATH.unlink(missing_ok=True)
    return {"released": True}

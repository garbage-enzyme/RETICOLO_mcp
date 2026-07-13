"""Lightweight solver lease for RETICOLO MCP.

Ensures only one MATLAB/RETICOLO solver owns the machine at a time.
Also checks for an active COMSOL MCP lease to prevent overlap.

Format (JSON, atomic write via temp+replace):
{
  "schema": "1",
  "owner": "reticolo-mcp",
  "pid": 12345,
  "created_at": 1752000000.0,
  "label": "job:<id>" or "interactive"
}
"""

from __future__ import annotations

import json
import os
import time
import uuid
import ctypes
from pathlib import Path
from typing import Any

from .config import LEASE_PATH, RUNTIME_DIR

LEASE_SCHEMA = "1"
COMSOL_LEASE_NAME = "solver_owner.json"


def _is_pid_alive(pid: int) -> bool:
    """Check if a Windows process with the given PID exists."""
    if pid <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
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
    if not _is_pid_alive(pid):
        return None
    return data


def _comsol_lease_path() -> Path | None:
    """Return the COMSOL MCP lease path if it exists in the shared runtime."""
    candidate = RUNTIME_DIR / COMSOL_LEASE_NAME
    if candidate.exists():
        return candidate
    alt = Path("D:\\comsol_runtime") / COMSOL_LEASE_NAME
    if alt.exists():
        return alt
    return None


def lease_status() -> dict[str, Any]:
    """Report current lease state. Read-only, no side effects."""
    our = _read_lease(LEASE_PATH)

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
    # A foreign reticolo lease (different PID from us) is also a block
    our_pid = os.getpid()
    foreign = _read_lease(LEASE_PATH)
    if foreign and foreign.get("pid") != our_pid:
        collision = True
        blockers.append({
            "owner": foreign.get("owner", "reticolo-mcp"),
            "lease": str(LEASE_PATH),
            "pid": foreign.get("pid"),
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


def lease_acquire(label: str = "interactive") -> dict[str, Any]:
    """Acquire the solver lease. Refuses if another owner is active.

    Args:
        label: Human-readable label, e.g. "job:<id>" or "interactive".

    Returns:
        {acquired: bool, collision: ..., detail: ...}
    """
    status = lease_status()
    if status["collision"]:
        return {"acquired": False, "collision": True,
                "blockers": status["blockers"]}

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": LEASE_SCHEMA,
        "owner": "reticolo-mcp",
        "pid": os.getpid(),
        "created_at": time.time(),
        "label": label,
    }

    tmp = LEASE_PATH.with_name(
        f".{LEASE_PATH.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, LEASE_PATH)

    return {"acquired": True, "collision": False,
            "lease_path": str(LEASE_PATH), "pid": data["pid"]}


def lease_release() -> dict[str, Any]:
    """Release the solver lease if we own it."""
    our = _read_lease(LEASE_PATH)
    if our is None:
        return {"released": False, "detail": "no active lease"}

    our_pid = os.getpid()
    if our.get("pid") != our_pid:
        return {"released": False, "detail": "lease owned by another process"}

    LEASE_PATH.unlink(missing_ok=True)
    return {"released": True}

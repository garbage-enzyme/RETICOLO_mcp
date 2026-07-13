"""Durable job store for RETICOLO MCP.

Each job lives at <runtime>/jobs/<job_id>/ with immutable spec,
atomic state, append-only events, and incremental results.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .config import RUNTIME_DIR

SCHEMA_VERSION = "1"
MAX_SPEC_BYTES = 256 * 1024
MAX_JOB_ID_LEN = 128


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON via temp file + os.replace."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)


def _ensure_job_dir(job_id: str) -> Path:
    """Create and return the job directory."""
    root = RUNTIME_DIR / "jobs" / job_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def _compute_spec_hash(spec: dict[str, Any]) -> str:
    """Deterministic SHA-256 over the spec payload."""
    canonical = json.dumps(spec, sort_keys=True, ensure_ascii=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------
# spec
# ------------------------------------------------------------------

def create_job_spec(
    wls_um: list[float],
    D: list[float],
    nn: list[int],
    textures: list[Any],
    profil: dict[str, list],
    polarization: int = 1,
    config_hash: str = "",
    config_label: str = "",
    mode: str = "memory",
) -> dict[str, Any]:
    """Build and validate an immutable job specification."""
    spec = {
        "schema": SCHEMA_VERSION,
        "job_type": "staged_sweep",
        "created_at": time.time(),
        "wls_um": [round(float(w), 9) for w in wls_um],
        "D": [round(float(v), 9) for v in D],
        "nn": [int(v) for v in nn],
        "textures": _normalize_textures(textures),
        "profil_heights": [round(float(v), 9) for v in profil.get("heights", [])],
        "profil_indices": [int(v) for v in profil.get("indices", [])],
        "polarization": int(polarization),
        "config_hash": config_hash,
        "config_label": config_label,
        "mode": mode,
    }
    if not spec["config_hash"]:
        spec["config_hash"] = _compute_spec_hash(spec)

    payload = json.dumps(spec, sort_keys=True)
    if len(payload.encode("utf-8")) > MAX_SPEC_BYTES:
        raise ValueError(f"spec too large: {len(payload)} bytes")
    return spec


def write_spec(job_id: str, spec: dict[str, Any]) -> Path:
    """Persist the immutable spec. Raises if spec already exists and differs."""
    root = _ensure_job_dir(job_id)
    spec_path = root / "spec.json"
    if spec_path.exists():
        existing = json.loads(spec_path.read_text(encoding="utf-8"))
        existing_hash = _compute_spec_hash(existing)
        new_hash = _compute_spec_hash(spec)
        if existing_hash != new_hash:
            raise ValueError(
                f"job {job_id}: spec changed. existing={existing_hash[:12]} "
                f"new={new_hash[:12]}")
        return spec_path
    _atomic_write_json(spec_path, spec)
    return spec_path


def read_spec(job_id: str) -> dict[str, Any] | None:
    """Read an existing job spec."""
    path = _ensure_job_dir(job_id) / "spec.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------
# state
# ------------------------------------------------------------------

VALID_STATES = frozenset({
    "submitted", "starting", "running", "completed",
    "failed", "interrupted", "cancel_requested",
})


def write_state(job_id: str, state: dict[str, Any]) -> None:
    """Atomically write current job state."""
    s = {k: v for k, v in state.items()}
    s.setdefault("status", "submitted")
    s.setdefault("updated_at", time.time())
    if s["status"] not in VALID_STATES:
        raise ValueError(f"invalid status: {s['status']}")
    _atomic_write_json(_ensure_job_dir(job_id) / "state.json", s)


def read_state(job_id: str) -> dict[str, Any] | None:
    """Read current job state."""
    path = _ensure_job_dir(job_id) / "state.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------
# events
# ------------------------------------------------------------------

def append_event(job_id: str, event: dict[str, Any]) -> None:
    """Append one event to the job journal, flushed and fsynced."""
    event.setdefault("timestamp", time.time())
    event.setdefault("event_id", uuid.uuid4().hex[:12])
    path = _ensure_job_dir(job_id) / "events.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_events(job_id: str, tail: int = 50) -> list[dict[str, Any]]:
    """Return the last N events."""
    path = _ensure_job_dir(job_id) / "events.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    events = []
    for line in lines[-tail:]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


# ------------------------------------------------------------------
# results
# ------------------------------------------------------------------

def results_path(job_id: str) -> Path:
    return _ensure_job_dir(job_id) / "results.csv"


def worker_log_path(job_id: str) -> Path:
    return _ensure_job_dir(job_id) / "worker.log"


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------

def _normalize_textures(textures: list[Any]) -> list[Any]:
    result = []
    for tex in textures:
        if isinstance(tex, (int, float, complex)):
            c = complex(tex)
            result.append([round(c.real, 9), round(c.imag, 9)])
        elif isinstance(tex, (list, tuple)):
            sub = []
            for item in tex:
                if isinstance(item, (int, float, complex)):
                    c = complex(item)
                    sub.append([round(c.real, 9), round(c.imag, 9)])
                elif isinstance(item, (list, tuple)):
                    sub.append([round(float(x), 9) for x in item])
                else:
                    sub.append(str(item))
            result.append(sub)
        else:
            result.append(str(tex))
    return result

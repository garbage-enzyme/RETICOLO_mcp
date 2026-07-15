"""Durable job store for RETICOLO MCP.

Each job lives at <runtime>/jobs/<job_id>/ with immutable spec,
atomic state, append-only events, and incremental results.
"""

from __future__ import annotations

import hashlib
import ctypes
import json
import os
import re
import time
import uuid
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import RUNTIME_DIR
from .durable_io import atomic_write_bytes

SCHEMA_VERSION = "1"
MAX_SPEC_BYTES = 256 * 1024
MAX_JOB_ID_LEN = 128
MAX_EVENT_TAIL = 100
MAX_EVENT_BYTES = 16 * 1024
MAX_EVENT_JOURNAL_BYTES = 8 * 1024 * 1024
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON via a flushed temporary file and atomic replacement."""
    payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_bytes(path, payload)


def _validate_job_id(job_id: str) -> str:
    """Validate an opaque job identifier without touching the filesystem."""
    if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
        raise ValueError("invalid job_id")
    if job_id in {".", ".."} or Path(job_id).is_absolute():
        raise ValueError("invalid job_id")
    return job_id


def _job_dir(job_id: str, *, create: bool = False) -> Path:
    """Resolve a contained job directory; reads never create it."""
    safe_id = _validate_job_id(job_id)
    jobs_root = (RUNTIME_DIR / "jobs").resolve()
    root = (jobs_root / safe_id).resolve()
    if root.parent != jobs_root:
        raise ValueError("job_id escapes runtime root")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _compute_spec_hash(spec: dict[str, Any]) -> str:
    """Deterministic SHA-256 over the spec payload."""
    canonical = json.dumps(spec, sort_keys=True, ensure_ascii=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _physical_identity_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """Return only normalized physical inputs for canonical configuration identity."""
    return {
        "schema": spec.get("schema", SCHEMA_VERSION),
        "D": spec.get("D", []),
        "nn": spec.get("nn", []),
        "textures": spec.get("textures", []),
        "profil_heights": spec.get("profil_heights", []),
        "profil_indices": spec.get("profil_indices", []),
        "polarization": spec.get("polarization", 1),
    }


def _job_identity_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """Return immutable job semantics, excluding timestamps and human labels."""
    return {
        "schema": spec.get("schema", SCHEMA_VERSION),
        "job_type": spec.get("job_type", "staged_sweep"),
        "physical_config_hash": spec.get("physical_config_hash")
        or spec.get("config_hash", ""),
        "wls_um": spec.get("wls_um", []),
        "mode": spec.get("mode", "memory"),
    }


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
    spec: dict[str, Any] = {
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
    physical_hash = config_hash or _compute_spec_hash(
        _physical_identity_payload(spec))
    spec["physical_config_hash"] = physical_hash
    job_hash = _compute_spec_hash(_job_identity_payload(spec))
    spec["job_spec_hash"] = job_hash
    spec["config_hash"] = job_hash  # compatibility alias used by sweep rows

    payload = json.dumps(spec, sort_keys=True)
    if len(payload.encode("utf-8")) > MAX_SPEC_BYTES:
        raise ValueError(f"spec too large: {len(payload)} bytes")
    return spec


def write_spec(job_id: str, spec: dict[str, Any]) -> Path:
    """Persist the immutable spec. Raises if spec already exists and differs."""
    root = _job_dir(job_id, create=True)
    spec_path = root / "spec.json"
    if spec_path.exists():
        existing = json.loads(spec_path.read_text(encoding="utf-8"))
        existing_hash = existing.get("job_spec_hash") or _compute_spec_hash(
            _job_identity_payload(existing))
        new_hash = spec.get("job_spec_hash") or _compute_spec_hash(
            _job_identity_payload(spec))
        if existing_hash != new_hash:
            raise ValueError(
                f"job {job_id}: spec changed. existing={existing_hash[:12]} "
                f"new={new_hash[:12]}")
        return spec_path
    _atomic_write_json(spec_path, spec)
    return spec_path


def read_spec(job_id: str) -> dict[str, Any] | None:
    """Read an existing job spec."""
    path = _job_dir(job_id) / "spec.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------
# state
# ------------------------------------------------------------------

VALID_STATES = frozenset({
    "submitted", "starting", "running", "completed",
    "completed_with_errors", "failed", "interrupted", "cancel_requested",
    "cancelling", "cancelled", "cleanup_uncertain", "resource_refused",
})


def transition_state(
    job_id: str, *, allowed_from: set[str] | frozenset[str],
    updates: dict[str, Any], attempt_id: str | None = None,
) -> dict[str, Any]:
    """Conditionally update state under a cross-process Windows named mutex."""
    mutex_name = "Global\\reticolo_mcp_job_" + hashlib.sha256(
        _validate_job_id(job_id).encode("utf-8")
    ).hexdigest()[:24]
    try:
        with _named_mutex(mutex_name):
            current = read_state(job_id)
            if current is None:
                return {"updated": False, "reason": "job_not_found", "state": None}
            if attempt_id is not None and current.get("attempt_id") != attempt_id:
                return {"updated": False, "reason": "stale_attempt", "state": current}
            if current.get("status") not in allowed_from:
                return {"updated": False, "reason": "invalid_transition", "state": current}
            new_state = {**current, **updates}
            write_state(job_id, new_state)
            return {"updated": True, "state": new_state}
    except TimeoutError:
        return {"updated": False, "reason": "mutex_timeout"}
    except OSError:
        return {"updated": False, "reason": "mutex_create_failed"}


def write_state(job_id: str, state: dict[str, Any]) -> None:
    """Atomically write current job state."""
    s = {k: v for k, v in state.items()}
    s.setdefault("status", "submitted")
    s["updated_at"] = time.time()
    if s["status"] not in VALID_STATES:
        raise ValueError(f"invalid status: {s['status']}")
    _atomic_write_json(_job_dir(job_id, create=True) / "state.json", s)


def read_state(job_id: str) -> dict[str, Any] | None:
    """Read current job state."""
    path = _job_dir(job_id) / "state.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------
# events
# ------------------------------------------------------------------

def append_event(job_id: str, event: dict[str, Any]) -> None:
    """Append one event to the job journal, flushed and fsynced."""
    path = _job_dir(job_id, create=True) / "events.jsonl"
    mutex_name = "Global\\reticolo_mcp_event_" + hashlib.sha256(
        _validate_job_id(job_id).encode("utf-8")
    ).hexdigest()[:24]
    with _named_mutex(mutex_name):
        previous = _last_event(path)
        record = dict(event)
        record.setdefault("timestamp", time.time())
        record.setdefault("event_id", uuid.uuid4().hex[:12])
        record["sequence"] = int(previous.get("sequence", 0)) + 1 if previous else 1
        record["previous_hash"] = previous.get("event_hash", "0" * 64) if previous else "0" * 64
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
        record["event_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        encoded = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > MAX_EVENT_BYTES:
            raise ValueError("event exceeds MAX_EVENT_BYTES")
        current_size = path.stat().st_size if path.exists() else 0
        if current_size + len(encoded) > MAX_EVENT_JOURNAL_BYTES:
            raise ValueError("event journal exceeds MAX_EVENT_JOURNAL_BYTES")
        with open(path, "ab") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())


def read_events(job_id: str, tail: int = 50) -> list[dict[str, Any]]:
    """Return the last N events."""
    if isinstance(tail, bool) or not isinstance(tail, int):
        raise ValueError("tail must be an integer")
    tail = max(0, min(tail, MAX_EVENT_TAIL))
    if tail == 0:
        return []
    path = _job_dir(job_id) / "events.jsonl"
    if not path.exists():
        return []
    lines: deque[str] = deque(maxlen=tail)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                lines.append(line)
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


def verify_event_chain(job_id: str) -> dict[str, Any]:
    path = _job_dir(job_id) / "events.jsonl"
    if not path.exists():
        return {"valid": True, "events": 0}
    expected_previous = "0" * 64
    expected_sequence = 1
    count = 0
    with open(path, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return {"valid": False, "line": line_number, "reason": "malformed"}
            stored_hash = record.pop("event_hash", "")
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
            actual_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if stored_hash != actual_hash:
                return {"valid": False, "line": line_number, "reason": "hash"}
            if record.get("previous_hash") != expected_previous:
                return {"valid": False, "line": line_number, "reason": "previous_hash"}
            if record.get("sequence") != expected_sequence:
                return {"valid": False, "line": line_number, "reason": "sequence"}
            expected_previous = stored_hash
            expected_sequence += 1
            count += 1
    return {"valid": True, "events": count, "last_hash": expected_previous}


# ------------------------------------------------------------------
# results
# ------------------------------------------------------------------

def results_path(job_id: str) -> Path:
    return _job_dir(job_id, create=True) / "results.csv"


def worker_log_path(job_id: str) -> Path:
    return _job_dir(job_id, create=True) / "worker.log"


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


def _last_event(path: Path) -> dict[str, Any] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    last = ""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                last = line
    if not last:
        return None
    try:
        event = json.loads(last)
    except json.JSONDecodeError as exc:
        raise ValueError("cannot append after malformed event") from exc
    if not isinstance(event, dict) or not event.get("event_hash"):
        raise ValueError("cannot append after unverifiable event")
    stored_hash = event.get("event_hash")
    content = dict(event)
    content.pop("event_hash", None)
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != stored_hash:
        raise ValueError("cannot append after tampered event")
    return event


@contextmanager
def _named_mutex(name: str):
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, name)
    if not mutex:
        raise OSError("cannot create named mutex")
    acquired = False
    try:
        wait_result = kernel32.WaitForSingleObject(mutex, 5000)
        acquired = wait_result in (0, 0x80)
        if not acquired:
            raise TimeoutError("named mutex timeout")
        yield
    finally:
        if acquired:
            kernel32.ReleaseMutex(mutex)
        kernel32.CloseHandle(mutex)

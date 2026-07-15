"""Solver-free caller-policy resource admission for RETICOLO jobs."""

from __future__ import annotations

import ctypes
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import MAX_JOB_POINTS, RUNTIME_DIR


class ResourcePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    min_available_memory_fraction: float = Field(ge=0, le=1)
    warning_available_memory_fraction: float = Field(ge=0, le=1)
    min_commit_remaining_fraction: float = Field(ge=0, le=1)
    warning_commit_remaining_fraction: float = Field(ge=0, le=1)
    min_runtime_free_fraction: float = Field(ge=0, le=1)
    warning_runtime_free_fraction: float = Field(ge=0, le=1)
    max_points: int = Field(ge=1, le=MAX_JOB_POINTS)
    wall_budget_s: float = Field(gt=0)
    min_next_point_time_s: float = Field(gt=0)

    @model_validator(mode="after")
    def warning_thresholds_must_not_be_lower(self) -> "ResourcePolicy":
        pairs = (
            (self.min_available_memory_fraction, self.warning_available_memory_fraction),
            (self.min_commit_remaining_fraction, self.warning_commit_remaining_fraction),
            (self.min_runtime_free_fraction, self.warning_runtime_free_fraction),
        )
        if any(warning < minimum for minimum, warning in pairs):
            raise ValueError("warning threshold must be >= refusal threshold")
        if self.min_next_point_time_s > self.wall_budget_s:
            raise ValueError("min_next_point_time_s exceeds wall_budget_s")
        return self


class ResourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    available_memory_fraction: float | None = Field(default=None, ge=0, le=1)
    commit_remaining_fraction: float | None = Field(default=None, ge=0, le=1)
    runtime_free_fraction: float | None = Field(default=None, ge=0, le=1)
    remaining_wall_s: float | None = Field(default=None, ge=0)


def evaluate_admission(
    policy: ResourcePolicy, snapshot: ResourceSnapshot, *, point_count: int,
) -> dict[str, Any]:
    policy_payload = policy.model_dump(mode="json")
    policy_hash = hashlib.sha256(json.dumps(
        policy_payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    values = snapshot.model_dump(mode="json")
    missing = sorted(key for key, value in values.items() if value is None)
    if missing:
        return _bind_decision({
            "decision": "refuse", "reason": "required_metric_unavailable",
            "missing_metrics": missing,
        }, policy_hash, values)
    if point_count < 1 or point_count > policy.max_points:
        return _bind_decision({
            "decision": "refuse", "reason": "point_limit",
        }, policy_hash, values)

    refusal_checks = {
        "available_memory": values["available_memory_fraction"] < policy.min_available_memory_fraction,
        "commit_remaining": values["commit_remaining_fraction"] < policy.min_commit_remaining_fraction,
        "runtime_free": values["runtime_free_fraction"] < policy.min_runtime_free_fraction,
        "wall_budget": values["remaining_wall_s"] < policy.min_next_point_time_s,
    }
    refused = sorted(key for key, failed in refusal_checks.items() if failed)
    if refused:
        return _bind_decision({
            "decision": "refuse", "reason": "threshold",
            "failed": refused,
        }, policy_hash, values)

    warning_checks = {
        "available_memory": values["available_memory_fraction"] < policy.warning_available_memory_fraction,
        "commit_remaining": values["commit_remaining_fraction"] < policy.warning_commit_remaining_fraction,
        "runtime_free": values["runtime_free_fraction"] < policy.warning_runtime_free_fraction,
    }
    warnings = sorted(key for key, failed in warning_checks.items() if failed)
    return _bind_decision({
        "decision": "warning" if warnings else "green",
        "reason": "threshold" if warnings else "all_thresholds_satisfied",
        "warnings": warnings,
    }, policy_hash, values)


def _bind_decision(
    result: dict[str, Any], policy_hash: str, snapshot: dict[str, Any],
) -> dict[str, Any]:
    bound = {**result, "policy_hash": policy_hash, "snapshot": snapshot}
    payload = json.dumps(bound, sort_keys=True, separators=(",", ":"))
    bound["decision_hash"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return bound


def sample_resources(
    *, runtime_dir: Path = RUNTIME_DIR, remaining_wall_s: float,
) -> ResourceSnapshot:
    memory = _memory_status()
    disk = shutil.disk_usage(runtime_dir.anchor or runtime_dir)
    return ResourceSnapshot(
        available_memory_fraction=(
            memory["available_physical"] / memory["total_physical"]
            if memory else None
        ),
        commit_remaining_fraction=(
            memory["available_pagefile"] / memory["total_pagefile"]
            if memory else None
        ),
        runtime_free_fraction=disk.free / disk.total if disk.total else None,
        remaining_wall_s=remaining_wall_s,
    )


def _memory_status() -> dict[str, int] | None:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return {
        "total_physical": int(status.ullTotalPhys),
        "available_physical": int(status.ullAvailPhys),
        "total_pagefile": int(status.ullTotalPageFile),
        "available_pagefile": int(status.ullAvailPageFile),
    }

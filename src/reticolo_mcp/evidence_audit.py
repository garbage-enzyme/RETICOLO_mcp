"""Solver-free audit of archived external convergence evidence."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_ROWS = 2_000_000


class ExternalEvidenceError(ValueError):
    """Raised when an archived evidence bundle is incomplete or inconsistent."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit_external_evidence_bundle(
    *,
    manifest_path: str | Path,
    points_path: str | Path,
    summary_path: str | Path,
    script_path: str | Path,
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    max_rows: int = DEFAULT_MAX_ROWS,
    balance_tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Validate and hash one archived convergence evidence bundle.

    The receipt proves internal consistency of files supplied to this audit. It
    deliberately does not claim that RETICOLO MCP produced the archived solve or
    that the scientific convergence criteria passed.
    """
    if max_artifact_bytes <= 0 or max_rows <= 0:
        raise ExternalEvidenceError("audit bounds must be positive")
    if not math.isfinite(balance_tolerance) or balance_tolerance < 0:
        raise ExternalEvidenceError("balance_tolerance must be finite and nonnegative")

    paths = {
        "manifest": _bounded_file(manifest_path, max_artifact_bytes),
        "points": _bounded_file(points_path, max_artifact_bytes),
        "summary": _bounded_file(summary_path, max_artifact_bytes),
        "script": _bounded_file(script_path, max_artifact_bytes),
    }
    resolved = [path.resolve(strict=True) for path in paths.values()]
    if len(set(resolved)) != len(resolved):
        raise ExternalEvidenceError("bundle roles must resolve to distinct files")

    try:
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExternalEvidenceError("manifest must be valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise ExternalEvidenceError("manifest root must be an object")

    config_id = _required_text(manifest, "config_id")
    script_sha256 = _required_hash(manifest, "script_sha256")
    config_text = _required_text(manifest, "config_text")
    if f"script={script_sha256}" not in config_text:
        raise ExternalEvidenceError("config_text is not bound to script_sha256")
    if sha256_file(paths["script"]) != script_sha256:
        raise ExternalEvidenceError("script SHA-256 does not match manifest")

    points = _audit_points_csv(
        paths["points"],
        config_id=config_id,
        script_sha256=script_sha256,
        max_rows=max_rows,
        balance_tolerance=balance_tolerance,
    )
    summary = _audit_summary_csv(
        paths["summary"], config_id=config_id, max_rows=max_rows,
    )
    artifacts = {
        role: {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for role, path in paths.items()
    }
    artifacts["points"]["row_count"] = points["row_count"]
    artifacts["summary"]["row_count"] = summary["row_count"]

    return {
        "schema_version": "external-evidence-audit/1.0",
        "status": "accepted",
        "evidence_classification": "external_archived_evidence_not_mcp_execution",
        "capability_promotion_allowed": False,
        "config_id": config_id,
        "script_sha256": script_sha256,
        "artifacts": artifacts,
        "point_evidence": points,
        "summary_evidence": summary,
        "limitations": [
            "This receipt does not prove that RETICOLO MCP produced the solve.",
            "Scientific peak and convergence criteria require a separate audit.",
        ],
    }


def _bounded_file(path: str | Path, max_bytes: int) -> Path:
    candidate = Path(path)
    try:
        if not candidate.is_file():
            raise ExternalEvidenceError(f"artifact is not a file: {candidate}")
        size = candidate.stat().st_size
    except OSError as exc:
        raise ExternalEvidenceError(f"artifact is unavailable: {candidate}") from exc
    if size > max_bytes:
        raise ExternalEvidenceError(f"artifact exceeds {max_bytes} bytes: {candidate}")
    return candidate


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExternalEvidenceError(f"manifest {key} must be a nonempty string")
    return value


def _required_hash(payload: dict[str, Any], key: str) -> str:
    value = _required_text(payload, key)
    if not _SHA256_RE.fullmatch(value):
        raise ExternalEvidenceError(f"manifest {key} must be lowercase SHA-256")
    return value


def _reader(path: Path, required: set[str]) -> tuple[csv.DictReader, Any]:
    try:
        handle = path.open("r", newline="", encoding="utf-8-sig")
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        if not fields or len(fields) != len(set(fields)):
            raise ExternalEvidenceError(f"CSV header is missing or duplicated: {path}")
        missing = sorted(required - set(fields))
        if missing:
            raise ExternalEvidenceError(f"CSV is missing columns {missing}: {path}")
        return reader, handle
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ExternalEvidenceError(f"CSV cannot be read: {path}") from exc


def _audit_points_csv(
    path: Path,
    *,
    config_id: str,
    script_sha256: str,
    max_rows: int,
    balance_tolerance: float,
) -> dict[str, Any]:
    reader, handle = _reader(
        path, {"config_id", "status", "script_sha256", "R", "T"},
    )
    try:
        fields = set(reader.fieldnames or [])
        absorption_column = "A_balance" if "A_balance" in fields else "A"
        if absorption_column not in fields:
            raise ExternalEvidenceError("points CSV requires A or A_balance")
        row_count = 0
        max_balance_error = 0.0
        for row_number, row in enumerate(reader, start=2):
            row_count += 1
            if row_count > max_rows:
                raise ExternalEvidenceError(f"points CSV exceeds {max_rows} rows")
            if row.get("config_id") != config_id:
                raise ExternalEvidenceError(f"points config_id mismatch at row {row_number}")
            if row.get("script_sha256") != script_sha256:
                raise ExternalEvidenceError(
                    f"points script_sha256 mismatch at row {row_number}"
                )
            if row.get("status") != "ok":
                raise ExternalEvidenceError(f"points status is not ok at row {row_number}")
            r_value = _finite_float(row, "R", row_number)
            t_value = _finite_float(row, "T", row_number)
            a_value = _finite_float(row, absorption_column, row_number)
            balance_error = abs(a_value - (1.0 - r_value - t_value))
            max_balance_error = max(max_balance_error, balance_error)
            if balance_error > balance_tolerance:
                raise ExternalEvidenceError(
                    f"derived absorption mismatch at row {row_number}"
                )
        if row_count == 0:
            raise ExternalEvidenceError("points CSV has no data rows")
        return {
            "row_count": row_count,
            "absorption_column": absorption_column,
            "absorption_definition": "derived_1_minus_R_minus_T",
            "max_balance_error": max_balance_error,
            "all_status_ok": True,
        }
    except csv.Error as exc:
        raise ExternalEvidenceError(f"malformed points CSV: {path}") from exc
    finally:
        handle.close()


def _audit_summary_csv(path: Path, *, config_id: str, max_rows: int) -> dict[str, Any]:
    reader, handle = _reader(path, {"config_id"})
    try:
        row_count = 0
        for row_number, row in enumerate(reader, start=2):
            row_count += 1
            if row_count > max_rows:
                raise ExternalEvidenceError(f"summary CSV exceeds {max_rows} rows")
            if row.get("config_id") != config_id:
                raise ExternalEvidenceError(f"summary config_id mismatch at row {row_number}")
        if row_count == 0:
            raise ExternalEvidenceError("summary CSV has no data rows")
        return {"row_count": row_count, "all_config_ids_match": True}
    except csv.Error as exc:
        raise ExternalEvidenceError(f"malformed summary CSV: {path}") from exc
    finally:
        handle.close()


def _finite_float(row: dict[str, str], key: str, row_number: int) -> float:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExternalEvidenceError(f"invalid {key} at row {row_number}") from exc
    if not math.isfinite(value):
        raise ExternalEvidenceError(f"nonfinite {key} at row {row_number}")
    return value

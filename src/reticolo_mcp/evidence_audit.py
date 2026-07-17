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


class ScientificEvidenceError(ExternalEvidenceError):
    """A coded failure to satisfy the archived scientific evidence schema."""

    def __init__(self, error_code: str, detail: str):
        super().__init__(detail)
        self.error_code = error_code


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


def audit_peak_convergence_claims(
    *,
    points_path: str | Path,
    summary_path: str | Path,
    group_column: str,
    point_order_columns: tuple[str, str] = ("nn_x", "nn_y"),
    summary_order_column: str = "nn",
    tol_center_nm: float,
    tol_absorption: float,
    tol_fwhm_relative: float,
    max_pair_order_gap: int = 2,
    numeric_tolerance: float = 1e-9,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> dict[str, Any]:
    """Reconstruct archived peak metrics and convergence decisions from raw rows."""
    tolerances = (tol_center_nm, tol_absorption, tol_fwhm_relative, numeric_tolerance)
    if not all(math.isfinite(value) and value >= 0 for value in tolerances):
        raise ExternalEvidenceError("convergence tolerances must be finite and nonnegative")
    if max_pair_order_gap <= 0 or max_rows <= 0:
        raise ExternalEvidenceError("convergence bounds must be positive")

    try:
        point_rows = _load_csv_rows(
            Path(points_path),
            {
                group_column, *point_order_columns, "wl_um", "R", "T", "status",
            },
            max_rows=max_rows,
        )
    except ExternalEvidenceError as exc:
        raise ScientificEvidenceError(
            "scientific_points_schema_invalid", str(exc),
        ) from exc
    point_fields = set(point_rows[0])
    absorption_column = "A_balance" if "A_balance" in point_fields else "A"
    if absorption_column not in point_fields:
        raise ExternalEvidenceError("points CSV requires A or A_balance")

    summary_required = {
        group_column, summary_order_column, "peak_wl_um", "peak_R", "peak_T",
        "peak_A", "FWHM_um", "Q", "baseline_A", "half_level_A",
        "peak_state", "width_state", "local_peak_count", "prev_shift_nm",
        "prev_A_change", "prev_FWHM_rel_change", "pair_converged",
    }
    try:
        summary_rows = _load_csv_rows(
            Path(summary_path), summary_required, max_rows=max_rows,
        )
    except ExternalEvidenceError as exc:
        raise ScientificEvidenceError(
            "scientific_summary_schema_invalid", str(exc),
        ) from exc

    spectra: dict[tuple[str, int], dict[float, tuple[float, float, float]]] = {}
    for row_number, row in enumerate(point_rows, start=2):
        if row["status"] != "ok":
            continue
        group = row[group_column]
        if not group:
            raise ExternalEvidenceError(f"empty {group_column} at point row {row_number}")
        orders = [_integer(row[column], column, row_number) for column in point_order_columns]
        if orders[0] != orders[1]:
            raise ExternalEvidenceError(f"anisotropic point order at row {row_number}")
        wavelength = _finite_float(row, "wl_um", row_number)
        values = (
            _finite_float(row, "R", row_number),
            _finite_float(row, "T", row_number),
            _finite_float(row, absorption_column, row_number),
        )
        spectra.setdefault((group, orders[0]), {})[wavelength] = values

    previous_by_group: dict[str, dict[str, Any]] = {}
    last_order_by_group: dict[str, int] = {}
    groups_with_pair: set[str] = set()
    reconstructed_rows = 0
    insufficient_rows = 0
    for row_number, row in enumerate(summary_rows, start=2):
        group = row[group_column]
        order = _integer(row[summary_order_column], summary_order_column, row_number)
        last_order = last_order_by_group.get(group)
        if last_order is not None and order <= last_order:
            raise ExternalEvidenceError(
                f"summary orders are not strictly increasing for {group} at row {row_number}"
            )
        last_order_by_group[group] = order
        claimed_state = row["peak_state"]
        if claimed_state == "insufficient":
            _require_nan_fields(
                row,
                ("peak_wl_um", "peak_R", "peak_T", "peak_A", "FWHM_um", "Q"),
                row_number,
            )
            if _bool01(row["pair_converged"], "pair_converged", row_number):
                raise ExternalEvidenceError(
                    f"insufficient summary row claims convergence at row {row_number}"
                )
            insufficient_rows += 1
            continue

        spectrum = spectra.get((group, order))
        if not spectrum or len(spectrum) < 3:
            raise ExternalEvidenceError(
                f"summary row {row_number} lacks three raw points for {group} order {order}"
            )
        claimed_wavelength = _finite_float(row, "peak_wl_um", row_number)
        peak = _reconstruct_peak(spectrum, claimed_wavelength)
        expected = {
            "peak_wl_um": peak["wl_um"],
            "peak_R": peak["R"],
            "peak_T": peak["T"],
            "peak_A": peak["A"],
            "FWHM_um": peak["fwhm_um"],
            "Q": peak["Q"],
            "baseline_A": peak["baseline_A"],
            "half_level_A": peak["half_level_A"],
            "local_peak_count": float(peak["local_peak_count"]),
        }
        if row["peak_state"] != peak["peak_state"]:
            raise ExternalEvidenceError(f"peak_state mismatch at summary row {row_number}")
        if row["width_state"] != peak["width_state"]:
            raise ExternalEvidenceError(f"width_state mismatch at summary row {row_number}")
        for column, value in expected.items():
            _require_number_match(row, column, value, numeric_tolerance, row_number)

        prior = previous_by_group.get(group)
        pair = False
        if prior is None:
            _require_nan_fields(
                row, ("prev_shift_nm", "prev_A_change", "prev_FWHM_rel_change"),
                row_number,
            )
        else:
            shift_nm = 1000.0 * abs(peak["wl_um"] - prior["wl_um"])
            delta_absorption = abs(peak["A"] - prior["A"])
            widths_finite = (
                math.isfinite(peak["fwhm_um"])
                and math.isfinite(prior["fwhm_um"])
                and prior["fwhm_um"] > 0
            )
            width_relative = (
                abs(peak["fwhm_um"] - prior["fwhm_um"]) / prior["fwhm_um"]
                if widths_finite else math.nan
            )
            _require_close(row, "prev_shift_nm", shift_nm, numeric_tolerance, row_number)
            _require_close(
                row, "prev_A_change", delta_absorption, numeric_tolerance, row_number,
            )
            _require_number_match(
                row, "prev_FWHM_rel_change", width_relative, numeric_tolerance, row_number,
            )
            pair = (
                0 < order - prior["order"] <= max_pair_order_gap
                and prior["width_state"] == "bracketed"
                and peak["width_state"] == "bracketed"
                and shift_nm <= tol_center_nm
                and delta_absorption <= tol_absorption
                and width_relative <= tol_fwhm_relative
            )
        claimed_pair = _bool01(row["pair_converged"], "pair_converged", row_number)
        if claimed_pair != pair:
            raise ExternalEvidenceError(f"pair_converged mismatch at summary row {row_number}")
        if pair:
            groups_with_pair.add(group)
        previous_by_group[group] = {**peak, "order": order}
        reconstructed_rows += 1

    groups = sorted({row[group_column] for row in summary_rows})
    missing_pairs = sorted(set(groups) - groups_with_pair)
    return {
        "status": "accepted" if not missing_pairs else "convergence_not_reached",
        "evidence_classification": "scientific_claims_reconstructed_from_archived_rows",
        "summary_rows_reconstructed": reconstructed_rows,
        "summary_rows_insufficient": insufficient_rows,
        "group_count": len(groups),
        "groups_with_converged_pair": sorted(groups_with_pair),
        "groups_without_converged_pair": missing_pairs,
        "all_groups_have_converged_pair": not missing_pairs,
        "tolerances": {
            "center_nm": tol_center_nm,
            "absorption": tol_absorption,
            "fwhm_relative": tol_fwhm_relative,
            "max_pair_order_gap": max_pair_order_gap,
        },
    }


def evaluate_peak_convergence_contract(**kwargs: Any) -> dict[str, Any]:
    """Return a stable acceptance or non-acceptance result for a scientific audit."""
    try:
        result = audit_peak_convergence_claims(**kwargs)
    except ScientificEvidenceError as exc:
        return {
            "status": "scientific_contract_not_satisfied",
            "accepted": False,
            "error_code": exc.error_code,
            "detail": str(exc)[:500],
        }
    except ExternalEvidenceError as exc:
        return {
            "status": "scientific_contract_not_satisfied",
            "accepted": False,
            "error_code": "scientific_claim_mismatch",
            "detail": str(exc)[:500],
        }
    result["accepted"] = result["status"] == "accepted"
    if not result["accepted"]:
        result["error_code"] = "convergence_not_reached"
    return result
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


def _load_csv_rows(path: Path, required: set[str], *, max_rows: int) -> list[dict[str, str]]:
    reader, handle = _reader(path, required)
    try:
        rows: list[dict[str, str]] = []
        for row in reader:
            rows.append(row)
            if len(rows) > max_rows:
                raise ExternalEvidenceError(f"CSV exceeds {max_rows} rows: {path}")
        if not rows:
            raise ExternalEvidenceError(f"CSV has no data rows: {path}")
        return rows
    except csv.Error as exc:
        raise ExternalEvidenceError(f"malformed CSV: {path}") from exc
    finally:
        handle.close()


def _integer(value: str, column: str, row_number: int) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ExternalEvidenceError(f"invalid {column} at row {row_number}") from exc
    if not math.isfinite(number) or not number.is_integer():
        raise ExternalEvidenceError(f"invalid {column} at row {row_number}")
    return int(number)


def _bool01(value: str, column: str, row_number: int) -> bool:
    if value not in {"0", "1"}:
        raise ExternalEvidenceError(f"{column} must be 0 or 1 at row {row_number}")
    return value == "1"


def _require_nan_fields(row: dict[str, str], columns: tuple[str, ...], row_number: int) -> None:
    for column in columns:
        try:
            value = float(row[column])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExternalEvidenceError(f"invalid {column} at row {row_number}") from exc
        if not math.isnan(value):
            raise ExternalEvidenceError(f"expected NaN {column} at row {row_number}")


def _require_close(
    row: dict[str, str], column: str, expected: float, tolerance: float, row_number: int,
) -> None:
    actual = _finite_float(row, column, row_number)
    if abs(actual - expected) > tolerance:
        raise ExternalEvidenceError(f"{column} mismatch at summary row {row_number}")


def _require_number_match(
    row: dict[str, str], column: str, expected: float, tolerance: float, row_number: int,
) -> None:
    if math.isnan(expected):
        _require_nan_fields(row, (column,), row_number)
    else:
        _require_close(row, column, expected, tolerance, row_number)


def _reconstruct_peak(
    spectrum: dict[float, tuple[float, float, float]], claimed_wavelength: float,
) -> dict[str, Any]:
    rows = sorted((wavelength, *values) for wavelength, values in spectrum.items())
    local = [
        index for index in range(1, len(rows) - 1)
        if rows[index][3] > rows[index - 1][3]
        and rows[index][3] >= rows[index + 1][3]
    ]
    candidates = local or [max(range(len(rows)), key=lambda index: rows[index][3])]
    peak_index = min(candidates, key=lambda index: abs(rows[index][0] - claimed_wavelength))
    wavelength, r_value, t_value, a_value = rows[peak_index]
    if peak_index == 0:
        state = "boundary_low"
    elif peak_index == len(rows) - 1:
        state = "boundary_high"
    else:
        state = "own_peak"
    result: dict[str, Any] = {
        "wl_um": wavelength,
        "R": r_value,
        "T": t_value,
        "A": a_value,
        "peak_state": state,
        "width_state": "not_run",
        "local_peak_count": len(local),
        "fwhm_um": math.nan,
        "Q": math.nan,
        "baseline_A": math.nan,
        "half_level_A": math.nan,
    }
    if state != "own_peak":
        return result

    baseline = 0.5 * (
        min(row[3] for row in rows[:peak_index])
        + min(row[3] for row in rows[peak_index + 1:])
    )
    half_level = baseline + 0.5 * (a_value - baseline)
    result["baseline_A"] = baseline
    result["half_level_A"] = half_level
    left_indices = [
        index for index in range(peak_index) if rows[index][3] <= half_level
    ]
    right_indices = [
        index for index in range(peak_index, len(rows))
        if rows[index][3] <= half_level
    ]
    if not left_indices or not right_indices:
        result["width_state"] = "unbracketed"
        return result
    left_index = left_indices[-1]
    right_index = right_indices[0]
    left_cross = _linear_cross(rows[left_index], rows[left_index + 1], half_level)
    right_cross = _linear_cross(rows[right_index - 1], rows[right_index], half_level)
    width = right_cross - left_cross
    if math.isfinite(width) and width > 0:
        result["fwhm_um"] = width
        result["Q"] = wavelength / width
        result["width_state"] = "bracketed"
    else:
        result["width_state"] = "unbracketed"
    return result


def _linear_cross(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
    level: float,
) -> float:
    x1, y1 = left[0], left[3]
    x2, y2 = right[0], right[3]
    if abs(y2 - y1) < math.ulp(1.0):
        return 0.5 * (x1 + x2)
    return x1 + (level - y1) * (x2 - x1) / (y2 - y1)

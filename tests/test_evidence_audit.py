"""Solver-free tests for archived external evidence auditing."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from reticolo_mcp.evidence_audit import (
    ExternalEvidenceError,
    ScientificEvidenceError,
    audit_external_evidence_bundle,
    audit_peak_convergence_claims,
    evaluate_peak_convergence_contract,
)


def _bundle(tmp_path: Path) -> dict[str, Path]:
    script = tmp_path / "driver.m"
    script.write_text("disp('fixture');\n", encoding="utf-8")
    script_hash = hashlib.sha256(script.read_bytes()).hexdigest()
    config_id = "fixture-001"

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "config_id": config_id,
        "config_text": f"fixture|script={script_hash}",
        "script_sha256": script_hash,
    }), encoding="utf-8")

    points = tmp_path / "points.csv"
    with points.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "config_id", "status", "script_sha256", "wl_um", "R", "T", "A",
        ])
        writer.writeheader()
        writer.writerow({
            "config_id": config_id, "status": "ok", "script_sha256": script_hash,
            "wl_um": "5.0", "R": "0.2", "T": "0.3", "A": "0.5",
        })

    summary = tmp_path / "summary.csv"
    with summary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["config_id", "peak_wl_um"])
        writer.writeheader()
        writer.writerow({"config_id": config_id, "peak_wl_um": "5.0"})
    return {"manifest": manifest, "points": points, "summary": summary, "script": script}


def _audit(paths: dict[str, Path], **kwargs):
    return audit_external_evidence_bundle(
        manifest_path=paths["manifest"],
        points_path=paths["points"],
        summary_path=paths["summary"],
        script_path=paths["script"],
        **kwargs,
    )


def test_valid_bundle_is_hashed_without_capability_promotion(tmp_path):
    receipt = _audit(_bundle(tmp_path))
    assert receipt["status"] == "accepted"
    assert receipt["evidence_classification"] == (
        "external_archived_evidence_not_mcp_execution"
    )
    assert receipt["capability_promotion_allowed"] is False
    assert receipt["artifacts"]["points"]["row_count"] == 1
    assert receipt["point_evidence"]["max_balance_error"] == pytest.approx(0.0)


def test_script_tamper_is_rejected(tmp_path):
    paths = _bundle(tmp_path)
    paths["script"].write_text("disp('changed');\n", encoding="utf-8")
    with pytest.raises(ExternalEvidenceError, match="script SHA-256"):
        _audit(paths)


@pytest.mark.parametrize("column,value,error", [
    ("config_id", "other", "config_id mismatch"),
    ("status", "error", "status is not ok"),
    ("script_sha256", "0" * 64, "script_sha256 mismatch"),
    ("R", "NaN", "nonfinite R"),
    ("A", "0.4", "derived absorption mismatch"),
])
def test_invalid_point_evidence_is_rejected(tmp_path, column, value, error):
    paths = _bundle(tmp_path)
    rows = list(csv.DictReader(paths["points"].read_text(encoding="utf-8").splitlines()))
    rows[0][column] = value
    with paths["points"].open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ExternalEvidenceError, match=error):
        _audit(paths)


def test_summary_config_mismatch_is_rejected(tmp_path):
    paths = _bundle(tmp_path)
    paths["summary"].write_text(
        "config_id,peak_wl_um\nother,5.0\n", encoding="utf-8",
    )
    with pytest.raises(ExternalEvidenceError, match="summary config_id mismatch"):
        _audit(paths)


def test_artifact_size_bound_is_enforced(tmp_path):
    paths = _bundle(tmp_path)
    with pytest.raises(ExternalEvidenceError, match="exceeds"):
        _audit(paths, max_artifact_bytes=10)


def _convergence_fixture(tmp_path: Path) -> tuple[Path, Path]:
    points = tmp_path / "convergence_points.csv"
    point_fields = [
        "density_label", "nn_x", "nn_y", "wl_um", "R", "T", "A", "status",
    ]
    with points.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=point_fields)
        writer.writeheader()
        for order, center, peak_absorption in ((19, 5.000, 0.900), (21, 5.001, 0.905)):
            half_level = 0.1 + 0.5 * (peak_absorption - 0.1)
            for offset, absorption in (
                (-0.020, 0.1), (-0.010, half_level), (0.0, peak_absorption),
                (0.010, half_level), (0.020, 0.1),
            ):
                writer.writerow({
                    "density_label": "n1", "nn_x": order, "nn_y": order,
                    "wl_um": center + offset, "R": 1.0 - absorption, "T": 0.0,
                    "A": absorption, "status": "ok",
                })

    summary = tmp_path / "convergence_summary.csv"
    fields = [
        "density_label", "nn", "peak_wl_um", "peak_R", "peak_T", "peak_A",
        "FWHM_um", "Q", "baseline_A", "half_level_A", "peak_state",
        "width_state", "local_peak_count", "prev_shift_nm", "prev_A_change",
        "prev_FWHM_rel_change", "pair_converged",
    ]
    with summary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "density_label": "n1", "nn": 19, "peak_wl_um": 5.0,
            "peak_R": 0.1, "peak_T": 0.0, "peak_A": 0.9,
            "FWHM_um": 0.02, "Q": 250.0, "baseline_A": 0.1,
            "half_level_A": 0.5, "peak_state": "own_peak",
            "width_state": "bracketed", "local_peak_count": 1,
            "prev_shift_nm": "NaN", "prev_A_change": "NaN",
            "prev_FWHM_rel_change": "NaN", "pair_converged": 0,
        })
        writer.writerow({
            "density_label": "n1", "nn": 21, "peak_wl_um": 5.001,
            "peak_R": 0.095, "peak_T": 0.0, "peak_A": 0.905,
            "FWHM_um": 0.02, "Q": 250.05, "baseline_A": 0.1,
            "half_level_A": 0.5025, "peak_state": "own_peak",
            "width_state": "bracketed", "local_peak_count": 1,
            "prev_shift_nm": 1.0, "prev_A_change": 0.005,
            "prev_FWHM_rel_change": 0.0, "pair_converged": 1,
        })
    return points, summary


def _audit_convergence(points: Path, summary: Path):
    return audit_peak_convergence_claims(
        points_path=points,
        summary_path=summary,
        group_column="density_label",
        tol_center_nm=10.0,
        tol_absorption=0.02,
        tol_fwhm_relative=0.1,
    )


def test_peak_and_pair_claims_are_reconstructed_from_raw_rows(tmp_path):
    points, summary = _convergence_fixture(tmp_path)
    receipt = _audit_convergence(points, summary)
    assert receipt["status"] == "accepted"
    assert receipt["summary_rows_reconstructed"] == 2
    assert receipt["groups_with_converged_pair"] == ["n1"]
    assert receipt["all_groups_have_converged_pair"] is True


def test_tampered_fwhm_claim_is_rejected(tmp_path):
    points, summary = _convergence_fixture(tmp_path)
    text = summary.read_text(encoding="utf-8").replace("0.02,250.05", "0.03,250.05")
    summary.write_text(text, encoding="utf-8")
    with pytest.raises(ExternalEvidenceError, match="FWHM_um mismatch"):
        _audit_convergence(points, summary)


def test_false_pair_claim_is_rejected(tmp_path):
    points, summary = _convergence_fixture(tmp_path)
    rows = list(csv.DictReader(summary.read_text(encoding="utf-8").splitlines()))
    rows[-1]["pair_converged"] = "0"
    with summary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ExternalEvidenceError, match="pair_converged mismatch"):
        _audit_convergence(points, summary)


def test_anisotropic_order_is_rejected_by_isotropic_contract(tmp_path):
    points, summary = _convergence_fixture(tmp_path)
    text = points.read_text(encoding="utf-8").replace("n1,19,19", "n1,19,21", 1)
    points.write_text(text, encoding="utf-8")
    with pytest.raises(ExternalEvidenceError, match="anisotropic"):
        _audit_convergence(points, summary)


def test_nonincreasing_summary_order_is_rejected(tmp_path):
    points, summary = _convergence_fixture(tmp_path)
    rows = list(csv.DictReader(summary.read_text(encoding="utf-8").splitlines()))
    rows[-1]["nn"] = "19"
    with summary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ExternalEvidenceError, match="strictly increasing"):
        _audit_convergence(points, summary)


def test_missing_scientific_summary_columns_have_stable_nonacceptance(tmp_path):
    points, summary = _convergence_fixture(tmp_path)
    rows = list(csv.DictReader(summary.read_text(encoding="utf-8").splitlines()))
    fields = [field for field in rows[0] if field != "FWHM_um"]
    with summary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    result = evaluate_peak_convergence_contract(
        points_path=points,
        summary_path=summary,
        group_column="density_label",
        tol_center_nm=10.0,
        tol_absorption=0.02,
        tol_fwhm_relative=0.1,
    )
    assert result["accepted"] is False
    assert result["status"] == "scientific_contract_not_satisfied"
    assert result["error_code"] == "scientific_summary_schema_invalid"
    assert "FWHM_um" in result["detail"]


def test_scientific_schema_exception_exposes_code(tmp_path):
    points, summary = _convergence_fixture(tmp_path)
    summary.write_text("density_label,nn\nn1,19\n", encoding="utf-8")
    with pytest.raises(ScientificEvidenceError) as caught:
        _audit_convergence(points, summary)
    assert caught.value.error_code == "scientific_summary_schema_invalid"

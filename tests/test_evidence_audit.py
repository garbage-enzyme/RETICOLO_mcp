"""Solver-free tests for archived external evidence auditing."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from reticolo_mcp.evidence_audit import (
    ExternalEvidenceError,
    audit_external_evidence_bundle,
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

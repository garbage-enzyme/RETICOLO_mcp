"""Solver-free capability/deployment receipt tests."""

from __future__ import annotations

import sys
from pathlib import Path

from reticolo_mcp.capabilities import _source_identity, capability_receipt
from reticolo_mcp.server import reticolo_capabilities


def test_receipt_is_solver_free_and_complete():
    sys.modules.pop("matlab", None)
    sys.modules.pop("matlab.engine", None)
    receipt = reticolo_capabilities()
    assert receipt["matlab_imported"] is False
    assert receipt["tool_count"] == len(receipt["tool_names"])
    assert "reticolo_capabilities" in receipt["tool_names"]
    assert "reticolo_resource_preflight" in receipt["tool_names"]
    assert set(receipt["tool_names"]) == set(receipt["tool_maturity"])
    assert len(receipt["build_identity_sha256"]) == 64
    assert len(receipt["typed_solve_schema_sha256"]) == 64
    assert receipt["experimental_enabled"] is False


def test_unknown_tool_is_not_silently_promoted():
    receipt = capability_receipt(["future_tool"])
    assert receipt["tool_maturity"]["future_tool"] == "unknown"


def test_field_and_convergence_are_not_marked_verified():
    receipt = reticolo_capabilities()
    assert receipt["tool_maturity"]["reticolo_field_export"].startswith("unavailable")
    assert (
        receipt["tool_maturity"]["reticolo_convergence"]
        == "experimental_not_release_accepted"
    )
    assert any(
        "external evidence" in limitation and "does not promote" in limitation
        for limitation in receipt["known_limitations"]
    )


def test_lifecycle_tools_are_promoted_after_real_receipts():
    receipt = reticolo_capabilities()
    assert receipt["tool_maturity"]["reticolo_start"] == "verified_real_lifecycle"
    assert receipt["tool_maturity"]["reticolo_stop"] == "verified_real_lifecycle"


def test_resource_preflight_includes_staged_real_gate():
    receipt = reticolo_capabilities()
    assert (
        receipt["tool_maturity"]["reticolo_resource_preflight"]
        == "verified_solver_free_and_staged_real"
    )


def test_one_point_reports_exact_verified_fixture_scope():
    receipt = reticolo_capabilities()
    assert (
        receipt["tool_maturity"]["reticolo_solve_point"]
        == "verified_te_tm_one_point_translation"
    )


def test_source_identity_is_stable_across_checkout_line_endings(tmp_path: Path):
    lf = tmp_path / "lf"
    crlf = tmp_path / "crlf"
    lf.mkdir()
    crlf.mkdir()
    (lf / "a.py").write_bytes(b"value = 1\n")
    (crlf / "a.py").write_bytes(b"value = 1\r\n")
    assert _source_identity(lf) == _source_identity(crlf)

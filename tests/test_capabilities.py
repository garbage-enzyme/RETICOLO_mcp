"""Solver-free capability/deployment receipt tests."""

from __future__ import annotations

import sys

from reticolo_mcp.capabilities import capability_receipt
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
    assert receipt["tool_maturity"]["reticolo_convergence"].startswith("experimental")

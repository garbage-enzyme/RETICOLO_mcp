"""Tests for the solver-free installed stdio acceptance utility."""

from pathlib import Path

import pytest

from scripts.verify_installed_transport import (
    _matlab_pids_from_tasklist,
    evaluate_receipt,
    validate_external_ascii_cwd,
    write_receipt,
)


def test_tasklist_parser_is_bounded_to_matlab():
    text = (
        '"MATLAB.exe","123","Console","1","100 K"\n'
        '"python.exe","456","Console","1","200 K"\n'
        '"MATLAB.exe","42","Console","1","300 K"\n'
    )
    assert _matlab_pids_from_tasklist(text) == [42, 123]


def test_evaluate_receipt_requires_exact_installed_identity():
    tools = ["a", "b"]
    payload = {
        "deployment_classification": "installed_site_package",
        "package_version": "1.2.3",
        "tool_count": 2,
        "tool_names": tools,
        "build_identity_sha256": "build",
        "typed_solve_schema_sha256": "schema",
        "experimental_enabled": False,
        "matlab_imported": False,
    }
    checks = evaluate_receipt(
        payload,
        tools,
        expected_version="1.2.3",
        expected_tool_count=2,
        expected_build_id="build",
        expected_schema_id="schema",
        expected_experimental=False,
        matlab_before=[],
        matlab_after=[],
    )
    assert all(checks.values())
    payload["deployment_classification"] = "source_tree"
    assert not evaluate_receipt(
        payload,
        tools,
        expected_version="1.2.3",
        expected_tool_count=2,
        expected_build_id="build",
        expected_schema_id="schema",
        expected_experimental=False,
        matlab_before=[],
        matlab_after=[],
    )["installed_site_package"]


def test_external_ascii_cwd_rejects_source_tree(tmp_path, monkeypatch):
    ascii_root = Path("D:/reticolo_transport_test")
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: ascii_root)
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    with pytest.raises(ValueError, match="outside"):
        validate_external_ascii_cwd(tmp_path)


def test_write_receipt_is_exact_and_leaves_no_temp(tmp_path):
    output = tmp_path / "receipt.json"
    write_receipt(output, {"status": "pass"})
    assert output.read_text(encoding="utf-8") == '{\n  "status": "pass"\n}\n'
    assert list(tmp_path.glob("*.tmp")) == []

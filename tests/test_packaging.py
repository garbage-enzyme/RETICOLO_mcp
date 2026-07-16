"""Solver-free packaging boundary tests."""

from __future__ import annotations

import tomllib
import tarfile
import zipfile
from pathlib import Path

import pytest
from setuptools import find_namespace_packages

from scripts.build_release import inspect_artifact


def test_source_evidence_tree_is_excluded_from_distribution():
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    excludes = config["tool"]["setuptools"]["packages"]["find"]["exclude"]
    assert config["tool"]["setuptools"]["include-package-data"] is False
    assert config["project"]["license"] == "MIT"
    assert config["project"]["license-files"] == ["LICENSE", "NOTICE"]
    packages = find_namespace_packages(
        where=str(root / "src"), exclude=excludes,
    )
    assert packages == ["reticolo_mcp"]
    assert not any(name.startswith("reticolo_mcp.tests") for name in packages)
    manifest = (root / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    assert "prune src/reticolo_mcp/tests" in manifest


def test_release_inspection_rejects_stale_wheel_test_tree(tmp_path):
    wheel = tmp_path / "reticolo_mcp-0.2.0.dev1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("reticolo_mcp/__init__.py", "")
        archive.writestr("reticolo_mcp/tests/run_m1_m5.py", "")
    with pytest.raises(ValueError, match="forbidden release entries"):
        inspect_artifact(wheel)


def test_release_inspection_rejects_sdist_source_evidence(tmp_path):
    sdist = tmp_path / "reticolo_mcp-0.2.0.dev1.tar.gz"
    payload = tmp_path / "m1_m5_verify.py"
    payload.write_text("", encoding="utf-8")
    with tarfile.open(sdist, "w:gz") as archive:
        archive.add(
            payload,
            arcname="reticolo_mcp-0.2.0.dev1/src/reticolo_mcp/tests/m1_m5_verify.py",
        )
    with pytest.raises(ValueError, match="forbidden release entries"):
        inspect_artifact(sdist)

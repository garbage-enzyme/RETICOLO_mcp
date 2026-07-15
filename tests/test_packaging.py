"""Solver-free packaging boundary tests."""

from __future__ import annotations

import tomllib
from pathlib import Path

from setuptools import find_namespace_packages


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

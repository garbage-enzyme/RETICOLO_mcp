"""Solver-free packaging boundary tests."""

from __future__ import annotations

import tomllib
from pathlib import Path

from setuptools import find_namespace_packages


def test_source_evidence_tree_is_excluded_from_distribution():
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    excludes = config["tool"]["setuptools"]["packages"]["find"]["exclude"]
    packages = find_namespace_packages(
        where=str(root / "src"), exclude=excludes,
    )
    assert packages == ["reticolo_mcp"]
    assert not any(name.startswith("reticolo_mcp.tests") for name in packages)

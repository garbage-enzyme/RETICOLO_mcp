"""Solver-free capability and deployment identity for RETICOLO MCP."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .config import EXPERIMENTAL_ENABLED
from .schema import SolveSpec


TOOL_MATURITY = {
    "reticolo_capabilities": "verified_solver_free",
    "reticolo_resource_preflight": "verified_solver_free",
    "solver_status": "verified_read_only",
    "reticolo_status": "verified_read_only",
    "reticolo_start": "verified_real_lifecycle",
    "reticolo_stop": "verified_real_lifecycle",
    "reticolo_solve_point": "verified_te_tm_one_point_translation",
    "reticolo_sweep": "experimental",
    "job_submit": "experimental",
    "job_status": "experimental",
    "job_tail": "experimental",
    "job_cancel": "experimental_cooperative_boundary_only",
    "job_resume": "experimental",
    "reticolo_convergence": "experimental_not_release_accepted",
    "reticolo_field_export": "unavailable_on_failing_v10_path",
}


def capability_receipt(tool_names: Iterable[str]) -> dict[str, Any]:
    """Return bounded identity without importing or starting MATLAB."""
    names = sorted(set(tool_names))
    schema_payload = json.dumps(
        SolveSpec.model_json_schema(), sort_keys=True, ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    maturity = {name: TOOL_MATURITY.get(name, "unknown") for name in names}
    return {
        "schema": "reticolo_capability_receipt/1",
        "package_version": __version__,
        "deployment_classification": _deployment_classification(),
        "build_identity_sha256": _source_identity(),
        "typed_solve_schema_sha256": hashlib.sha256(schema_payload).hexdigest(),
        "tool_count": len(names),
        "tool_names": names,
        "tool_maturity": maturity,
        "experimental_enabled": EXPERIMENTAL_ENABLED,
        "matlab_imported": "matlab" in sys.modules or "matlab.engine" in sys.modules,
        "known_limitations": [
            "convergence is experimental and not branch-convergence accepted",
            "RETICOLO V10 field export fails on the current retchamp fixture",
        ],
    }


def _deployment_classification() -> str:
    package_dir = Path(__file__).resolve().parent
    repo_root = package_dir.parent.parent
    return "source_tree" if (repo_root / "pyproject.toml").is_file() else "installed_site_package"


def _source_identity(package_dir: Path | None = None) -> str:
    """Hash canonical package-relative Python names and LF-normalized bytes."""
    package_dir = package_dir or Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(package_dir.glob("*.py"), key=lambda p: p.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        source = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        digest.update(source)
        digest.update(b"\0")
    return digest.hexdigest()

"""Create a solver-free receipt for archived external convergence evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from reticolo_mcp.durable_io import atomic_write_bytes
from reticolo_mcp.evidence_audit import (
    audit_external_evidence_bundle,
    evaluate_peak_convergence_contract,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--points", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-artifact-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--max-rows", type=int, default=2_000_000)
    parser.add_argument("--convergence-group-column")
    parser.add_argument("--tol-center-nm", type=float)
    parser.add_argument("--tol-absorption", type=float)
    parser.add_argument("--tol-fwhm-relative", type=float)
    args = parser.parse_args()

    receipt = audit_external_evidence_bundle(
        manifest_path=args.manifest,
        points_path=args.points,
        summary_path=args.summary,
        script_path=args.script,
        max_artifact_bytes=args.max_artifact_bytes,
        max_rows=args.max_rows,
    )
    convergence_values = (
        args.tol_center_nm, args.tol_absorption, args.tol_fwhm_relative,
    )
    if args.convergence_group_column:
        if any(value is None for value in convergence_values):
            parser.error("all three convergence tolerances are required")
        receipt["scientific_convergence_audit"] = evaluate_peak_convergence_contract(
            points_path=args.points,
            summary_path=args.summary,
            group_column=args.convergence_group_column,
            tol_center_nm=args.tol_center_nm,
            tol_absorption=args.tol_absorption,
            tol_fwhm_relative=args.tol_fwhm_relative,
            max_rows=args.max_rows,
        )
        receipt["scientific_acceptance"] = receipt[
            "scientific_convergence_audit"
        ]["accepted"]
        if not receipt["scientific_acceptance"]:
            receipt["status"] = "provenance_accepted_scientific_not_accepted"
    elif any(value is not None for value in convergence_values):
        parser.error("--convergence-group-column is required with tolerances")
    payload = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_bytes(args.output, payload)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt.get("scientific_acceptance", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())

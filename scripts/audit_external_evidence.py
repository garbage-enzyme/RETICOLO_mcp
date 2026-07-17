"""Create a solver-free receipt for archived external convergence evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from reticolo_mcp.durable_io import atomic_write_bytes
from reticolo_mcp.evidence_audit import audit_external_evidence_bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--points", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-artifact-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--max-rows", type=int, default=2_000_000)
    args = parser.parse_args()

    receipt = audit_external_evidence_bundle(
        manifest_path=args.manifest,
        points_path=args.points,
        summary_path=args.summary,
        script_path=args.script,
        max_artifact_bytes=args.max_artifact_bytes,
        max_rows=args.max_rows,
    )
    payload = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_bytes(args.output, payload)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

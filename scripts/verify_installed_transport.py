"""Verify an installed RETICOLO MCP server through a fresh stdio transport.

This operator gate intentionally runs from an ASCII directory outside the source
tree. It checks initialization, tool discovery, the capability receipt, and the
MATLAB process set without importing ``reticolo_mcp`` in the verifier process.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _matlab_pids_from_tasklist(text: str) -> list[int]:
    pids: list[int] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2 and row[0].casefold() == "matlab.exe":
            pids.append(int(row[1]))
    return sorted(pids)


def matlab_pids() -> list[int]:
    completed = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq MATLAB.exe", "/FO", "CSV", "/NH"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return _matlab_pids_from_tasklist(completed.stdout)


def validate_external_ascii_cwd(cwd: Path) -> Path:
    resolved = cwd.resolve(strict=True)
    try:
        str(resolved).encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("--cwd must resolve to an ASCII-only path") from exc
    if (resolved / "src" / "reticolo_mcp").is_dir():
        raise ValueError("--cwd must be outside the RETICOLO MCP source tree")
    return resolved


def evaluate_receipt(
    payload: dict[str, Any],
    tool_names: list[str],
    *,
    expected_version: str,
    expected_tool_count: int,
    expected_build_id: str,
    expected_schema_id: str,
    expected_experimental: bool,
    matlab_before: list[int],
    matlab_after: list[int],
) -> dict[str, bool]:
    return {
        "installed_site_package": (
            payload.get("deployment_classification") == "installed_site_package"
        ),
        "version": payload.get("package_version") == expected_version,
        "tool_count": (
            len(tool_names) == expected_tool_count == payload.get("tool_count")
        ),
        "tool_names": tool_names == sorted(payload.get("tool_names", [])),
        "build_identity": payload.get("build_identity_sha256") == expected_build_id,
        "schema_identity": (
            payload.get("typed_solve_schema_sha256") == expected_schema_id
        ),
        "experimental_flag": (
            payload.get("experimental_enabled") is expected_experimental
        ),
        "matlab_not_imported": payload.get("matlab_imported") is False,
        "matlab_process_set_unchanged": matlab_before == matlab_after,
    }


def write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        with path.open("rb") as handle:
            if handle.read() != data:
                raise OSError("receipt readback mismatch")
    finally:
        temporary.unlink(missing_ok=True)


async def run_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    cwd = validate_external_ascii_cwd(args.cwd)
    matlab_before = matlab_pids()
    env = dict(os.environ)
    env["RETICOLO_MCP_DIR"] = str(args.reticolo_dir.resolve(strict=True))
    if args.experimental:
        env["RETICOLO_MCP_ENABLE_EXPERIMENTAL"] = "1"
    else:
        env.pop("RETICOLO_MCP_ENABLE_EXPERIMENTAL", None)
    params = StdioServerParameters(
        command=str(args.python.resolve(strict=True)),
        args=["-m", "reticolo_mcp.server"],
        cwd=cwd,
        env=env,
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(
            reader,
            writer,
            read_timeout_seconds=timedelta(seconds=args.timeout_seconds),
        ) as session:
            initialized = await session.initialize()
            listed = await session.list_tools()
            called = await session.call_tool("reticolo_capabilities", {})
    matlab_after = matlab_pids()
    if called.isError:
        raise RuntimeError("reticolo_capabilities returned an MCP tool error")
    text_blocks = [getattr(block, "text", None) for block in called.content]
    text_blocks = [block for block in text_blocks if block is not None]
    if len(text_blocks) != 1:
        raise RuntimeError("reticolo_capabilities did not return one text block")
    payload = json.loads(text_blocks[0])
    tool_names = sorted(tool.name for tool in listed.tools)
    checks = evaluate_receipt(
        payload,
        tool_names,
        expected_version=args.expected_version,
        expected_tool_count=args.expected_tool_count,
        expected_build_id=args.expected_build_id,
        expected_schema_id=args.expected_schema_id,
        expected_experimental=args.experimental,
        matlab_before=matlab_before,
        matlab_after=matlab_after,
    )
    receipt = {
        "schema": "reticolo_stdio_acceptance/1",
        "server_name": initialized.serverInfo.name,
        "server_version": initialized.serverInfo.version,
        "protocol_version": initialized.protocolVersion,
        "tool_names": tool_names,
        "capability_receipt": payload,
        "matlab_pids_before": matlab_before,
        "matlab_pids_after": matlab_after,
        "checks": checks,
        "status": "pass" if all(checks.values()) else "fail",
    }
    if receipt["status"] != "pass":
        raise AssertionError(json.dumps(receipt, indent=2, sort_keys=True))
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--reticolo-dir", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-tool-count", type=int, required=True)
    parser.add_argument("--expected-build-id", required=True)
    parser.add_argument("--expected-schema-id", required=True)
    parser.add_argument("--experimental", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    receipt = asyncio.run(run_acceptance(args))
    if args.output is not None:
        write_receipt(args.output, receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

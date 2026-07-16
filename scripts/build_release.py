"""Build and inspect RETICOLO MCP release artifacts from a clean Git archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable


FORBIDDEN_PARTS = {"reticolo_v10", "m1_m5_results"}
FORBIDDEN_NAMES = {"m1_m5_verify.py", "run_m1_m5.py"}


def _normalized_archive_names(names: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for name in names:
        clean = name.replace("\\", "/").rstrip("/")
        if not clean:
            continue
        path = PurePosixPath(clean)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe archive path: {name}")
        normalized.append(clean)
    return sorted(normalized)


def inspect_artifact(path: Path) -> dict:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = _normalized_archive_names(archive.namelist())
        package_prefix = "reticolo_mcp/"
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            names = _normalized_archive_names(member.name for member in archive)
        roots = {name.split("/", 1)[0] for name in names}
        if len(roots) != 1:
            raise ValueError("sdist must contain exactly one top-level directory")
        package_prefix = f"{next(iter(roots))}/src/reticolo_mcp/"
    else:
        raise ValueError(f"unsupported artifact: {path.name}")

    forbidden = []
    for name in names:
        parts = set(PurePosixPath(name).parts)
        if parts & FORBIDDEN_PARTS or PurePosixPath(name).name in FORBIDDEN_NAMES:
            forbidden.append(name)
        if name.startswith(f"{package_prefix}tests/"):
            forbidden.append(name)
    if forbidden:
        raise ValueError(f"forbidden release entries: {sorted(set(forbidden))}")

    runtime_modules = [
        name for name in names
        if name.startswith(package_prefix)
        and name.count("/") == package_prefix.count("/")
        and name.endswith(".py")
    ]
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
        "entry_count": len(names),
        "runtime_module_count": len(runtime_modules),
        "forbidden_entry_count": 0,
    }


def _require_ascii(path: Path, *, must_exist: bool) -> Path:
    resolved = path.resolve(strict=must_exist)
    try:
        str(resolved).encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"path must be ASCII-only: {resolved}") from exc
    return resolved


def _run(command: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout


def _write_receipt(path: Path, receipt: dict) -> None:
    data = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if path.read_bytes() != data:
            raise OSError("release receipt readback mismatch")
    finally:
        temporary.unlink(missing_ok=True)


def build_release(args: argparse.Namespace) -> dict:
    source = _require_ascii(args.source, must_exist=True)
    work_root = _require_ascii(args.work_root, must_exist=True)
    output = _require_ascii(args.output, must_exist=False)
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    if (source / "src" / "reticolo_mcp").is_dir() is False:
        raise ValueError("source is not a RETICOLO MCP checkout")
    tracked_status = _run(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=source,
    )
    if tracked_status.strip():
        raise RuntimeError("tracked worktree must be clean before release build")
    commit = _run(["git", "rev-parse", args.commit], cwd=source).strip()

    temporary_root = Path(tempfile.mkdtemp(prefix="reticolo_release_", dir=work_root))
    try:
        archive_path = temporary_root / "source.zip"
        subprocess.run(
            ["git", "archive", "--format=zip", "-o", str(archive_path), commit],
            cwd=source,
            check=True,
        )
        clean_source = temporary_root / "source"
        clean_source.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            _normalized_archive_names(archive.namelist())
            archive.extractall(clean_source)
        build_output = temporary_root / "artifacts"
        build_output.mkdir()
        _run(
            [
                str(args.python.resolve(strict=True)), "-m", "build",
                str(clean_source), "--wheel", "--sdist", "--outdir",
                str(build_output),
            ],
            cwd=work_root,
        )
        artifacts = sorted(build_output.iterdir())
        if len(artifacts) != 2:
            raise RuntimeError(f"expected wheel and sdist, found {len(artifacts)}")
        inspections = [inspect_artifact(path) for path in artifacts]
        output.mkdir(parents=True)
        copied = []
        for path in artifacts:
            destination = output / path.name
            shutil.copy2(path, destination)
            copied.append(inspect_artifact(destination))
        receipt = {
            "schema": "reticolo_release_build_receipt/1",
            "commit": commit,
            "source": str(source),
            "clean_git_archive": True,
            "artifacts": copied,
            "status": "pass",
        }
        _write_receipt(output / "release_build_receipt.json", receipt)
        return receipt
    finally:
        shutil.rmtree(temporary_root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--commit", default="HEAD")
    return parser


def main() -> None:
    receipt = build_release(build_parser().parse_args())
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

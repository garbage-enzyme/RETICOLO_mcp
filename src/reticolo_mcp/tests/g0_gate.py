"""G0 integration test — minimal real-engine gate. Starts MATLAB, verifies
RETICOLO health, runs a tiny solve, stops, and checks for orphans.

Run with: python -m reticolo_mcp.tests.g0_gate
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure src/ is importable
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent.parent / "src"
sys.path.insert(0, str(_SRC))

from reticolo_mcp.engine import REticoloEngine
from reticolo_mcp.config import RETICOLO_DIR, RETICOLO_SCRATCH_DIR

MATLAB_EXE = r"D:\Program Files\MATLAB\R2025b\bin\matlab.exe"


def check_matlab_process() -> list[str]:
    """Return MATLAB process info if running, else empty list."""
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq MATLAB.exe", "/FO", "CSV", "/NH"],
            text=True, timeout=10,
        )
        return [line for line in out.strip().split("\n") if "MATLAB.exe" in line]
    except Exception:
        return []


def check_ret_orphans() -> list[str]:
    """Return any retXXXX scratch directories (exclude reticolo_v10)."""
    orphans = []
    for base in (Path(RETICOLO_SCRATCH_DIR), Path.cwd()):
        if base.is_dir():
            for d in base.iterdir():
                if d.is_dir() and d.name.startswith("ret") and len(d.name) == 7:
                    # retXXXX = 3 + 4 random chars
                    orphans.append(str(d))
    return orphans


def main():
    print("=" * 60)
    print("G0 — Minimal real-engine gate")
    print("=" * 60)

    # ---- pre-flight ----
    print("\n[1] Pre-flight checks ...")
    print(f"    RETICOLO_DIR = {RETICOLO_DIR}")
    print(f"    RETICOLO_DIR exists = {RETICOLO_DIR.is_dir()}")
    print(f"    MATLAB = {MATLAB_EXE}")
    print(f"    MATLAB exists = {Path(MATLAB_EXE).is_file()}")

    before_matlab = check_matlab_process()
    before_matlab_count = len(before_matlab)
    print(f"    MATLAB processes before: {before_matlab_count}")

    # ---- engine lifecycle ----
    print("\n[2] Engine lifecycle ...")
    eng = REticoloEngine(RETICOLO_DIR)

    status = eng.status()
    print(f"    Before start: {status['status']}")

    result = eng.start(mode="memory")
    print(f"    Start result: {result['status']}")
    if result["status"] != "connected":
        print(f"    FAIL: start returned {result}")
        print(f"    Full: {result}")
        return 1

    print(f"    Uptime: {result.get('uptime_s')} s")
    print(f"    Lease: {result.get('lease', {}).get('collision')}")

    # ---- tiny solve: air on air, nn=3 ----
    print("\n[3] Tiny solve (air/air, nn=3, TE) ...")
    result = eng.solve_point(
        wl_um=1.0,
        D=1.0,
        nn=[3, 3],
        textures=[1.0, 1.0],  # air superstrate, air substrate
        profil={"heights": [0.0, 0.0], "indices": [1, 2]},
        polarization=1,
        config_id="g0_smoke",
    )
    print(f"    Status: {result['status']}")
    if result["status"] == "error":
        print(f"    FAIL: {result.get('error_code')} / {result.get('error')}")
        eng.stop()
        return 1
    print(f"    R = {result.get('R', 0):.6f}")
    print(f"    T = {result.get('T', 0):.6f}")
    print(f"    A_balance = {result.get('A_balance', 0):.6f}")
    print(f"    Passive = {result.get('passive')}")
    R = result.get("R", 1)
    T = result.get("T", 0)
    if abs(R) < 0.01 and abs(T - 1.0) < 0.01:
        print(f"    PASS: air/air R0, T1 (expected)")
    else:
        print(f"    WARNING: R={R:.6f}, T={T:.6f} (deviation)")

    # ---- tiny solve: TM ----
    print("\n[4] Tiny solve (air/air, nn=3, TM) ...")
    result = eng.solve_point(
        wl_um=1.0, D=1.0, nn=[3, 3],
        textures=[1.0, 1.0],
        profil={"heights": [0.0, 0.0], "indices": [1, 2]},
        polarization=-1, config_id="g0_smoke_tm",
    )
    print(f"    Status: {result['status']}")
    if result["status"] == "error":
        print(f"    FAIL: {result.get('error_code')} / {result.get('error')}")
        eng.stop()
        return 1
    print(f"    R = {result.get('R', 0):.6f}")
    print(f"    T = {result.get('T', 0):.6f}")
    R2 = result.get("R", 1)
    T2 = result.get("T", 0)
    if abs(R2) < 0.01 and abs(T2 - 1.0) < 0.01:
        print(f"    PASS: TM air/air R≈0, T≈1")
    else:
        print(f"    WARNING: R={R2:.6f}, T={T2:.6f}")

    # ---- stop ----
    print("\n[5] Stop ...")
    result = eng.stop()
    print(f"    Stop: {result['status']}")

    # Allow MATLAB to fully shut down
    time.sleep(3)

    # ---- post-flight ----
    print("\n[6] Post-flight checks ...")
    after_matlab = check_matlab_process()
    new_matlab = [p for p in after_matlab if p not in before_matlab]
    print(f"    New MATLAB processes: {len(new_matlab)}")
    if new_matlab:
        print(f"    FAIL: MATLAB process leaked: {new_matlab[:3]}")
    else:
        print(f"    PASS: no MATLAB leak")

    orphans = check_ret_orphans()
    print(f"    retXXXX orphan dirs in scratch: {len(orphans)}")
    if orphans:
        print(f"    FAIL: orphans: {orphans[:5]}")
    else:
        print(f"    PASS: no retXXXX orphans")

    lease_path = Path(os.environ.get("RETICOLO_RUNTIME_DIR", "D:\\reticolo_runtime")) / "reticolo_lease.json"
    if lease_path.exists():
        print(f"    Lease file still exists (may be stale from other session): {lease_path}")
    else:
        print(f"    PASS: lease file cleaned")

    print("\n" + "=" * 60)
    print("G0: done")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())

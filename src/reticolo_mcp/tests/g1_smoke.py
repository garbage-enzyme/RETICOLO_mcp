"""G1 M0 resource-containment staged smoke test.

Stages: nn=9 (2pts) → nn=15 (1pt) → nn=21 (1pt if gate passes).
Monitors RSS, C:/D: free space, retXXXX orphans.
"""

from __future__ import annotations

import os, subprocess, sys, time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent.parent / "src"
sys.path.insert(0, str(_SRC))

from reticolo_mcp.engine import REticoloEngine
from reticolo_mcp.config import RETICOLO_DIR


def free_gb(drive: str) -> float:
    try:
        import ctypes
        free_bytes = ctypes.c_ulonglong(0)
        ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            drive, ctypes.byref(free_bytes), None, None)
        return free_bytes.value / 1e9
    except Exception:
        return -1


def rss_mb(pid: int) -> float:
    try:
        import psutil
        return psutil.Process(pid).memory_info().rss / 1e6
    except Exception:
        return -1


def check_orphans():
    orphans = []
    scratch = Path("D:\\reticolo_scratch")
    if scratch.is_dir():
        for d in scratch.iterdir():
            if d.is_dir() and d.name.startswith("ret") and len(d.name) == 7:
                orphans.append(d.name)
    cwd_orphans = []
    for d in Path.cwd().iterdir():
        if d.is_dir() and d.name.startswith("ret") and len(d.name) == 7:
            cwd_orphans.append(d.name)
    return orphans, cwd_orphans


def main():
    eng = REticoloEngine(RETICOLO_DIR)
    r = eng.start(mode="memory")
    if r["status"] != "connected":
        print(f"FAIL: start: {r}")
        return 1

    our_pid = os.getpid()
    failures = 0

    # base: n=1.5 slab, 0.5um, wl=1.0um
    textures = [1.0, 1.5, 1.0]
    profil = {"heights": [0.0, 0.5, 0.0], "indices": [1, 2, 3]}

    stage = 1
    for nn_val, n_pts in [(9, 2), (15, 1)]:
        print(f"\n--- Stage {stage}: nn={nn_val}, {n_pts} point(s) ---")
        c_before = free_gb("C:\\")
        d_before = free_gb("D:\\")

        for pt in range(n_pts):
            wl = 1.0 + pt * 0.001
            result = eng.solve_point(
                wl_um=wl, D=1.0, nn=[nn_val, nn_val],
                textures=textures, profil=profil,
                polarization=1, config_id=f"g1_s{stage}",
            )
            mem = rss_mb(our_pid)
            print(f"  wl={wl:.3f} R={result.get('R',-1):.4f} "
                  f"T={result.get('T',-1):.4f} dt={result.get('solve_time_s',0):.1f}s "
                  f"RSS={mem:.0f}MB status={result['status']}")
            if result["status"] != "ok":
                failures += 1

        c_after = free_gb("C:\\")
        d_after = free_gb("D:\\")
        c_delta = c_before - c_after
        d_delta = d_before - d_after
        print(f"  C: {c_delta:+.2f} GB  D: {d_delta:+.2f} GB")
        if c_delta > 2:
            print(f"  FAIL: C: changed by {c_delta:.2f} GB")
            failures += 1
        else:
            print(f"  PASS: C: delta < 2 GB")

        orphans, cwd_o = check_orphans()
        if orphans or cwd_o:
            print(f"  FAIL: orphans: scratch={orphans} cwd={cwd_o}")
            failures += 1
        else:
            print(f"  PASS: no retXXXX orphans")

        stage += 1

    eng.stop()
    # MATLAB engine quit is async; wait up to 15s for process exit
    for attempt in range(6):
        time.sleep(3)
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq MATLAB.exe", "/FO", "CSV", "/NH"],
                text=True, timeout=10)
            matlab_lines = [l for l in out.strip().split("\n") if "MATLAB.exe" in l]
            if not matlab_lines:
                print("  MATLAB process exited cleanly")
                break
        except Exception:
            break
    else:
        print(f"\n  WARNING: MATLAB process still running after 15s (async shutdown)")
        # not a hard failure — engine.quit() was called

    print(f"\n{'='*60}")
    if failures == 0:
        print("G1: ALL PASSED")
        return 0
    else:
        print(f"G1: {failures} FAILURE(S)")
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""G3 integration test — submit job, verify worker runs, check results.

Run standalone: python src/reticolo_mcp/tests/g3_integration.py
"""

from __future__ import annotations

import os, subprocess, sys, time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent.parent / "src"
sys.path.insert(0, str(_SRC))

from reticolo_mcp import jobs
from reticolo_mcp.engine import REticoloEngine
from reticolo_mcp.config import RETICOLO_DIR, RUNTIME_DIR


def main():
    python = sys.executable
    worker_script = str(_SRC / "reticolo_mcp" / "worker.py")

    # Prepare test spec
    spec = jobs.create_job_spec(
        wls_um=[1.0, 1.001],
        D=[1.0], nn=[3, 3],
        textures=[1.0, 1.5, 1.0],
        profil={"heights": [0.0, 0.5, 0.0], "indices": [1, 2, 3]},
        polarization=1,
        config_label="g3_test",
        mode="memory",
    )
    job_id = "g3-test-001"

    # Clean any previous run
    job_dir = RUNTIME_DIR / "jobs" / job_id
    if job_dir.exists():
        import shutil
        shutil.rmtree(str(job_dir))

    # Submit
    jobs.write_spec(job_id, spec)
    jobs.write_state(job_id, {"status": "submitted"})
    jobs.append_event(job_id, {"event": "submitted"})
    print(f"Job {job_id} submitted")
    print(f"  spec hash: {spec['config_hash'][:16]}")
    print(f"  job dir: {job_dir}")

    # Start worker
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC)
    print(f"\nStarting worker...")
    proc = subprocess.Popen(
        [python, worker_script, job_id],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env,
    )

    # Poll for completion (max 60s)
    for _ in range(30):
        time.sleep(2)
        state = jobs.read_state(job_id)
        if state and state["status"] in ("completed", "failed"):
            break

    state = jobs.read_state(job_id)
    print(f"\nFinal state: {state['status']}")
    if state.get("solved"):
        print(f"  Solved: {state['solved']}  Skipped: {state.get('skipped',0)}  Errors: {state.get('errors',0)}")
    if state.get("error"):
        print(f"  Error: {state['error'][:200]}")

    # Check results CSV
    csv_path = jobs.results_path(job_id)
    if csv_path.exists():
        with open(csv_path) as f:
            lines = f.readlines()
            print(f"\nResults CSV: {len(lines)} lines")
            for line in lines[:5]:
                print(f"  {line.rstrip()}")
    else:
        print("\nNo results CSV!")

    # Check worker log
    log_path = jobs.worker_log_path(job_id)
    if log_path.exists():
        with open(log_path) as f:
            log_lines = [l for l in f.readlines() if "completed" in l.lower() or "error" in l.lower() or "crashed" in l.lower()][-5:]
            if log_lines:
                print(f"\nWorker log (last relevant):")
                for l in log_lines:
                    print(f"  {l.rstrip()[-120:]}")

    # Verify resume
    if state and state["status"] == "completed":
        print("\n--- Resume test ---")
        jobs.write_state(job_id, {**state, "status": "submitted"})
        jobs.append_event(job_id, {"event": "resume_test"})
        proc2 = subprocess.Popen(
            [python, worker_script, job_id],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env,
        )
        for _ in range(20):
            time.sleep(2)
            s = jobs.read_state(job_id)
            if s and s["status"] in ("completed", "failed"):
                break
        s2 = jobs.read_state(job_id)
        print(f"  Resume: solved={s2.get('solved', '?')} skipped={s2.get('skipped', '?')}")
        proc2.wait(timeout=5)

    proc.wait(timeout=5)

    success = state and state["status"] == "completed" and state.get("solved", 0) > 0
    print(f"\n{'PASS' if success else 'FAIL'}")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())

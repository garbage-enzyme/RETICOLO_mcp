"""Quick G3 integration test — direct worker run."""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent.parent / "src"
sys.path.insert(0, str(_SRC))

from reticolo_mcp import jobs, worker

jid = "g3-direct-test"
spec = jobs.create_job_spec(
    wls_um=[1.0, 1.001], D=[1.0], nn=[3, 3],
    textures=[1.0, 1.5, 1.0],
    profil={"heights": [0.0, 0.5, 0.0], "indices": [1, 2, 3]},
    passivity_tolerance=1e-12,
    config_label="g3_direct", mode="memory",
)
jobs.write_spec(jid, spec)
jobs.write_state(jid, {"status": "submitted"})
jobs.append_event(jid, {"event": "submitted"})

exit_code = worker.main(job_id=jid)
print(f"Worker exit: {exit_code}")

state = jobs.read_state(jid)
print(f"State: {state['status']} solved={state.get('solved')} skipped={state.get('skipped')}")

import csv
csv_path = jobs.results_path(jid)
with open(csv_path) as f:
    for row in csv.DictReader(f):
        print(f"  wl={row['wl_um']} R={row['R']} T={row['T']} A={row['A_balance']}")

events = jobs.read_events(jid, tail=3)
for e in events:
    print(f"  event: {e.get('event')}")

print("PASS" if state["status"] == "completed" and state.get("solved") == 2 else "FAIL")
sys.exit(0 if state["status"] == "completed" else 1)

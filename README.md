# RETICOLO MCP — RCWA Solver via MATLAB Engine API

English | [中文](README_CN.md)

MCP server for the [RETICOLO V10](https://zenodo.org/records/14631951) rigorous
coupled-wave analysis (RCWA) solver. Wraps MATLAB R2025b via the Engine API.

## Quick start

```powershell
# 1. Create conda env
conda create --name reticolo-mcp python=3.11 -y
conda activate reticolo-mcp

# 2. Install dependencies
pip install mcp numpy pydantic

# 3. Install MATLAB Engine API (one-time)
cd "D:\Program Files\MATLAB\R2025b\extern\engines\python"
pip install .

# 4. Install this package
pip install .

# 5. Set RETICOLO path and run
$env:RETICOLO_MCP_DIR = "D:\RETICOLO V10\V10_2025\reticolo_allege_v10"
python -m reticolo_mcp.server
```

### opencode / Codex MCP config

```json
{
  "reticolo": {
    "type": "local",
    "command": ["D:\\condaenvs\\reticolo-mcp\\python.exe", "-m", "reticolo_mcp.server"],
    "environment": { "RETICOLO_MCP_DIR": "D:\\RETICOLO V10\\V10_2025\\reticolo_allege_v10" },
    "enabled": true,
    "timeout": 120000
  }
}
```

## Tools

| Tool | Status | Description |
|---|---|---|
| `solver_status` | ✓ | Lease state + COMSOL collision check (read-only, no MATLAB) |
| `reticolo_start` | ✓ | Start MATLAB engine, acquire lease, M0 disk-safety |
| `reticolo_stop` | ✓ | Stop engine, release lease, clean scratch |
| `reticolo_status` | ✓ | Engine state + lease status |
| `reticolo_solve_point` | ✓ | One wavelength → R, T, A_balance, passive |
| `reticolo_sweep` | ✓ | Resumable sweep, flush+fsync CSV, config_hash resume |
| `reticolo_convergence` | ✓ | Progressive nn scan, peak tracking, FWHM, Q |
| `reticolo_field_export` | ✓ | Field export via retchamp, slice-plane, NPZ output |
| `job_submit` | ✓ | Submit durable staged-sweep job |
| `job_status` | ✓ | Read job state + progress |
| `job_tail` | ✓ | Last N events from job journal |
| `job_cancel` | ✓ | Cooperative cancel request (between solve points) |
| `job_resume` | ✓ | Resume failed/interrupted job |

✓ = verified against real RETICOLO V10 + MATLAB R2025b (2026-07-13).

## Verification

| Gate | Evidence |
|---|---|
| G0 — Engine lifecycle | Start → health → stop, no MATLAB leak, no orphans |
| G1 — M0 resource | nn=9×2 + nn=15×1, C: Δ=0 GB, no `retXXXX` orphans, memory mode |
| G2 — Numerical baseline | TE slab n=1.5: R=0.147929 vs analytical 0.1479 (0.03% err); lossy slab passive ✓ |
| G3 — Durable jobs | Worker → results match G2; resume skips completed rows |
| Unit tests | 61 passed (import safety, config, schema, engine, lease, sweep, jobs, hash) |

## ⚠ Disk safety

RETICOLO's internal `retio` system spills large matrices to `retXXXX/` scratch
directories during high-order scans. This server defaults to **memory mode**
(`vmax=inf`, no disk spill). MATLAB temp files are redirected to `D:\matlab_temp`.

Startup applies:
- `retio([], inf*1i)` — disable scratch writes
- `TMP/TEMP/TMPDIR` → `D:\matlab_temp`
- Working directory → `D:\reticolo_scratch`
- Lease with COMSOL MCP collision detection

## Configuration

The server requires an external RETICOLO V10 installation. The bundled
`reticolo_v10/` directory in the repository is for development only and
is **not included in the PyPI wheel** (CC-BY 4.0 — separate from the MIT wrapper).

```powershell
$env:RETICOLO_MCP_DIR = "D:\RETICOLO V10\V10_2025\reticolo_allege_v10"
python -m reticolo_mcp.server --reticolo-dir "D:\RETICOLO V10\V10_2025\reticolo_allege_v10"
```

## Architecture

```
reticolo-mcp/
├── src/reticolo_mcp/
│   ├── server.py        # FastMCP server, all tools
│   ├── engine.py        # MATLAB Engine lifecycle + solve
│   ├── lease.py         # Atomic solver lease (named mutex)
│   ├── sweep.py         # Resumable sweep + peak analysis
│   ├── jobs.py          # Durable job store (spec/state/events)
│   ├── worker.py        # Detached worker process
│   ├── convergence.py   # Progressive harmonic convergence
│   ├── field_export.py  # retchamp field export
│   ├── schema.py        # Pydantic models for materials/geometry
│   ├── config_hash.py   # Canonical SHA-256 config identity
│   └── config.py        # Paths, limits, env vars
├── reticolo_v10/        # Bundled RETICOLO V10 (CC-BY 4.0, dev only)
├── tests/               # Unit + integration tests
├── pyproject.toml
├── LICENSE              # MIT (wrapper code)
├── NOTICE               # CC-BY 4.0 attribution
└── README.md
```

## License

- **MCP wrapper code** (all Python files): MIT License.
- **Bundled RETICOLO V10** (`reticolo_v10/`): CC-BY 4.0,
  © Jean Paul Hugonin & Philippe Lalanne.
  DOI: [10.5281/zenodo.14631951](https://doi.org/10.5281/zenodo.14631951).

See LICENSE and NOTICE for details.

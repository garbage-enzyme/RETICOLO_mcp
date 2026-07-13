# RETICOLO MCP

MCP server for the [RETICOLO V10](https://zenodo.org/records/14631951) rigorous
coupled-wave analysis (RCWA) solver. Wraps MATLAB R2025b via the Engine API so
agents can compute RCWA spectra, convergence scans, and field maps without
launching the MATLAB desktop.

## Quick start

```powershell
# 1. Create and activate the conda environment
conda create --name reticolo-mcp python=3.11 -y
conda activate reticolo-mcp

# 2. Install the MCP package
pip install mcp

# 3. Install MATLAB Engine API (one-time)
cd "D:\Program Files\MATLAB\R2025b\extern\engines\python"
pip install .

# 4. Install this package (non-editable)
pip install .

# 5. Run the server
python -m reticolo_mcp.server
```

## Configuration

The server expects RETICOLO V10 at `reticolo_v10/reticolo_allege_v10/` relative
to the repository root. The bundled copy is provided under the CC-BY 4.0 license
(see NOTICE).

Override the path with `RETICOLO_MCP_DIR`:
```powershell
$env:RETICOLO_MCP_DIR = "D:\RETICOLO V10\V10_2025\reticolo_allege_v10"
```

## Tools

| Tool | Description |
|---|---|
| `reticolo_start` | Start MATLAB engine, apply disk-safety settings |
| `reticolo_stop` | Stop engine and release license |
| `reticolo_status` | Report engine state |
| `reticolo_solve_point` | Solve one wavelength (R, T, A) |

## ⚠ Disk safety

RETICOLO's internal `retio` system spills large matrices to disk during high-
order scans. This server disables that behavior at startup (`vmax=Inf`) and
redirects MATLAB temp files to `D:\matlab_temp`. See `Desktop\plans\RETICOLO_MCP_development_plan.md`
for the complete M0 safety design.

## License

- **MCP wrapper code** (all Python files): MIT License.
- **Bundled RETICOLO V10** (`reticolo_v10/`): CC-BY 4.0,
  copyright Jean Paul Hugonin and Philippe Lalanne.
  DOI: [10.5281/zenodo.14631951](https://doi.org/10.5281/zenodo.14631951).

See LICENSE and NOTICE for details.

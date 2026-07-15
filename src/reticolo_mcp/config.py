"""RETICOLO MCP configuration.

All paths and defaults are resolved once at import time from environment
variables with safe fallbacks. Host-specific values stay in env vars,
not in this file.
"""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent

# -- RETICOLO directory --------------------------------------------------

def _resolve_reticolo_dir() -> Path:
    env = os.environ.get("RETICOLO_MCP_DIR", "")
    if env:
        return Path(env)
    bundled = REPO_ROOT / "reticolo_v10" / "reticolo_allege_v10"
    if bundled.is_dir():
        return bundled
    cwd_bundled = Path.cwd() / "reticolo_v10" / "reticolo_allege_v10"
    if cwd_bundled.is_dir():
        return cwd_bundled
    return bundled


RETICOLO_DIR = _resolve_reticolo_dir()

# -- MATLAB scratch and temp ---------------------------------------------

RETICOLO_SCRATCH_DIR = Path(
    os.environ.get("RETICOLO_SCRATCH_DIR", "D:\\reticolo_scratch")
)

MATLAB_TEMP_DIR = Path(
    os.environ.get("RETICOLO_MATLAB_TEMP", "D:\\matlab_temp")
)

# -- solver lease --------------------------------------------------------

# Share the same runtime root as COMSOL MCP when possible, otherwise use
# a dedicated root so the two servers can detect each other.
_COMSOL_RUNTIME = os.environ.get("COMSOL_MCP_RUNTIME_DIR", "")
if _COMSOL_RUNTIME:
    RUNTIME_DIR = Path(_COMSOL_RUNTIME)
else:
    RUNTIME_DIR = Path(
        os.environ.get("RETICOLO_RUNTIME_DIR", "D:\\reticolo_runtime")
    )

LEASE_PATH = RUNTIME_DIR / "reticolo_lease.json"

# -- limits ---------------------------------------------------------------

MAX_CONFIG_ID_LEN = 128
MAX_TEXTURES = 32
MAX_ERROR_CHARS = 500
MAX_JOB_POINTS = 2048
MAX_FOURIER_ORDER = 63

EXPERIMENTAL_ENABLED = os.environ.get(
    "RETICOLO_MCP_ENABLE_EXPERIMENTAL", ""
).strip().lower() in {"1", "true", "yes"}

"""pytest configuration for reticolo-mcp."""

import sys
from pathlib import Path

# Ensure src/ is importable without editable install
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

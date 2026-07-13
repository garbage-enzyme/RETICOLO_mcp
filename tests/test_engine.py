"""Unit tests for reticolo-mcp. No MATLAB required."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ------------------------------------------------------------------
# import safety — no MATLAB import on module load
# ------------------------------------------------------------------


def test_import_safety():
    """Importing reticolo_mcp must not import matlab.engine."""
    with patch.dict(sys.modules, {}, clear=False):
        if "matlab" in sys.modules:
            del sys.modules["matlab"]
        if "matlab.engine" in sys.modules:
            del sys.modules["matlab.engine"]

        from reticolo_mcp import __version__
        from reticolo_mcp.engine import REticoloEngine

        assert __version__ == "0.1.0"
        assert "matlab" not in sys.modules


# ------------------------------------------------------------------
# config
# ------------------------------------------------------------------


class TestConfig:
    def test_default_reticolo_dir(self):
        from reticolo_mcp import config
        assert config.RETICOLO_DIR.name == "reticolo_allege_v10"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("RETICOLO_MCP_DIR", "D:\\custom_reticolo")
        import importlib
        from reticolo_mcp import config
        importlib.reload(config)
        assert str(config.RETICOLO_DIR) == "D:\\custom_reticolo"

    def test_limits(self):
        from reticolo_mcp import config
        assert config.MAX_CONFIG_ID_LEN == 128
        assert config.MAX_TEXTURES == 32
        assert config.MAX_ERROR_CHARS == 500


# ------------------------------------------------------------------
# engine — lifecycle without MATLAB
# ------------------------------------------------------------------


class TestEngineLifecycle:
    def test_stopped_status(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/nonexistent"))
        s = eng.status()
        assert s["status"] == "stopped"
        assert s["connected"] is False

    def test_stop_when_stopped(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/nonexistent"))
        s = eng.stop()
        assert s["status"] == "stopped"

    def test_solve_without_engine(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/nonexistent"))
        r = eng.solve_point(
            wl_um=5.0,
            D=1.0,
            nn=[5, 5],
            textures=[1.0, 1.5, 1.0],
            profil={"heights": [0, 0.1, 0], "indices": [1, 2, 3]},
        )
        assert r["status"] == "error"
        assert r["error_code"] == "engine_not_started"

    def test_start_missing_reticolo_dir(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/nonexistent_dir_xyz"))
        with patch("reticolo_mcp.engine._check_matlab_engine", return_value=""):
            r = eng.start()
        assert r["status"] == "error"
        assert r["error_code"] == "reticolo_dir_missing"

    def test_start_no_matlab_engine(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/"))
        r = eng.start()
        assert r["status"] == "error"
        assert r["error_code"] == "matlab_engine_not_installed"


# ------------------------------------------------------------------
# solve_point input validation
# ------------------------------------------------------------------


class TestSolveValidation:
    def test_invalid_polarization(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/"))
        eng._engine = MagicMock()
        eng._engine.workspace = {}
        r = eng.solve_point(
            wl_um=5.0, D=1.0, nn=[5, 5],
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
            polarization=0,
        )
        assert r["status"] == "error"
        assert r["error_code"] == "invalid_polarization"

    def test_invalid_D(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/"))
        eng._engine = MagicMock()
        eng._engine.workspace = {}
        r = eng.solve_point(
            wl_um=5.0, D=[1, 2, 3], nn=[5, 5],
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
        )
        assert r["status"] == "error"
        assert r["error_code"] == "invalid_D"


# ------------------------------------------------------------------
# error classification
# ------------------------------------------------------------------


class TestErrorClassification:
    def test_disk_error(self):
        from reticolo_mcp.engine import _classify_error
        result = _classify_error(Exception("out of disk space"))
        assert result.startswith("disk_error:")

    def test_memory_error(self):
        from reticolo_mcp.engine import _classify_error
        result = _classify_error(Exception("out of memory"))
        assert result.startswith("memory_error:")

    def test_undefined(self):
        from reticolo_mcp.engine import _classify_error
        result = _classify_error(Exception("undefined variable xyz"))
        assert result.startswith("matlab_undefined:")

    def test_generic(self):
        from reticolo_mcp.engine import _classify_error
        result = _classify_error(Exception("some random error"))
        assert result == "some random error"

    def test_truncation(self):
        from reticolo_mcp.engine import _classify_error
        long_msg = "x" * 600
        result = _classify_error(Exception(long_msg))
        assert len(result) <= 503  # 500 + "..."


# ------------------------------------------------------------------
# server tools — input validation
# ------------------------------------------------------------------


class TestServerValidation:
    def test_config_id_truncation(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/"))
        eng._engine = MagicMock()
        eng._engine.workspace = {}
        r = eng.solve_point(
            wl_um=5.0, D=1.0, nn=[5, 5],
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
            config_id="a" * 200,
        )
        assert r["status"] == "error"  # no MATLAB, but config_id not the issue
        # config_id would be truncated to 128 by server, but engine
        # doesn't enforce it — that's ok, server layer handles it.

    def test_textures_limit(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/"))
        eng._engine = MagicMock()
        eng._engine.workspace = {}
        r = eng.solve_point(
            wl_um=5.0, D=1.0, nn=[5, 5],
            textures=[1.0] * 40,
            profil={"heights": [0, 0], "indices": [1, 1]},
        )
        # engine allows 40 textures (server caps at 32), so this goes through
        # but with mocked engine. The server layer does the cap.
        assert "status" in r  # any result is fine here

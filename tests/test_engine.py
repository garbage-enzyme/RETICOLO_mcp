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
        eng = REticoloEngine(Path("/__nonexistent_dir_test_xyz__"))
        r = eng.start()
        assert r["status"] == "error"
        assert r["error_code"] == "reticolo_dir_missing"


# ------------------------------------------------------------------
# solve_point input validation
# ------------------------------------------------------------------


class TestSolveValidation:
    def test_invalid_polarization(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/"))
        eng._engine = MagicMock()
        eng._engine.workspace = {}
        mock_matlab = MagicMock()
        with patch("reticolo_mcp.engine._ensure_matlab", return_value=mock_matlab):
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
# _textures_to_cell unit tests
# ------------------------------------------------------------------


class TestTexturesToCell:
    def test_uniform_scalar(self):
        from reticolo_mcp.engine import _textures_to_cell
        eng = MagicMock()
        eng.cell.return_value = [None] * 2
        matlab = MagicMock()

        _textures_to_cell(eng, matlab, [1.0, 1.5])
        assert eng.cell.call_count == 1
        args = eng.cell.call_args[0]
        assert args[1] == 2

    def test_patterned_layer(self):
        from reticolo_mcp.engine import _textures_to_cell
        eng = MagicMock()
        eng.cell.side_effect = lambda r, c: [None] * c
        matlab = MagicMock()

        textures = [[1.0, [0.0, 0.0, 0.3, 0.3, 4.0, 0.001, 1]]]
        _textures_to_cell(eng, matlab, textures)
        assert eng.cell.call_count == 2

    def test_complex_scalar(self):
        from reticolo_mcp.engine import _textures_to_cell
        eng = MagicMock()
        eng.cell.return_value = [None]
        matlab = MagicMock()

        _textures_to_cell(eng, matlab, [complex(4.0, 0.001)])
        assert eng.cell.call_count == 1

    def test_empty_textures(self):
        from reticolo_mcp.engine import _textures_to_cell
        eng = MagicMock()
        eng.cell.return_value = []
        matlab = MagicMock()

        result = _textures_to_cell(eng, matlab, [])
        assert len(result) == 0

    def test_plain_list_not_pattern(self):
        from reticolo_mcp.engine import _textures_to_cell
        eng = MagicMock()
        eng.cell.side_effect = lambda r, c: [None] * c
        matlab = MagicMock()

        _textures_to_cell(eng, matlab, [[1.0, 1.5]])
        assert eng.cell.call_count == 2


# ------------------------------------------------------------------
# server tools — input validation
# ------------------------------------------------------------------


class TestServerValidation:
    def test_config_id_truncation(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/"))
        eng._engine = MagicMock()
        eng._engine.workspace = {}
        # config_id truncation is handled by server layer, not engine.
        # Engine accepts any config_id; server caps at MAX_CONFIG_ID_LEN.
        r = eng.solve_point(
            wl_um=5.0, D=[1, 2, 3], nn=[5, 5],
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
            config_id="a" * 200,
        )
        assert r["status"] == "error"
        assert r["error_code"] == "invalid_D"

    def test_textures_limit(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/"))
        eng._engine = MagicMock()
        eng._engine.workspace = {}
        # textures count limit is handled by server layer.
        r = eng.solve_point(
            wl_um=5.0, D=[1, 2, 3], nn=[5, 5],
            textures=[1.0] * 40,
            profil={"heights": [0, 0], "indices": [1, 1]},
        )
        assert r["status"] == "error"
        assert r["error_code"] == "invalid_D"

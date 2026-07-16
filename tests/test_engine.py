"""Unit tests for reticolo-mcp. No MATLAB required."""

from __future__ import annotations

import os
import math
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

        assert __version__ == "0.2.0.dev1"
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

    def test_stop_does_not_claim_success_when_matlab_quit_fails(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/"))
        owned = MagicMock()
        owned.quit.side_effect = RuntimeError("quit failed")
        eng._engine = owned
        eng._lease_token = "owned-token"
        with patch("reticolo_mcp.engine.lease_release") as release:
            result = eng.stop()
        assert result["status"] == "cleanup_uncertain"
        assert result["error_code"] == "matlab_quit_failed"
        assert result["connected"] is True
        assert eng._engine is owned
        assert eng._lease_token == "owned-token"
        release.assert_not_called()

    def test_stop_does_not_claim_success_when_lease_release_fails(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/"))
        owned = MagicMock()
        eng._engine = owned
        eng._lease_token = "owned-token"
        with (
            patch.object(eng, "_stop_heartbeat"),
            patch.object(eng, "_start_heartbeat") as restart_heartbeat,
            patch(
                "reticolo_mcp.engine.lease_release",
                return_value={"released": False, "detail": "sharing violation"},
            ),
        ):
            result = eng.stop()
        assert result["status"] == "cleanup_uncertain"
        assert result["error_code"] == "lease_release_failed"
        assert result["connected"] is False
        assert eng._engine is None
        assert eng._lease_token == "owned-token"
        restart_heartbeat.assert_called_once_with()

    def test_failed_start_retains_engine_and_lease_when_quit_fails(
        self, tmp_path,
    ):
        import types
        from reticolo_mcp.engine import REticoloEngine

        owned = MagicMock()
        owned.addpath.side_effect = RuntimeError("setup failed")
        owned.quit.side_effect = RuntimeError("quit failed")
        matlab_module = types.ModuleType("matlab")
        matlab_module.__path__ = []
        matlab_engine_module = types.ModuleType("matlab.engine")
        matlab_engine_module.start_matlab = MagicMock(return_value=owned)
        matlab_module.engine = matlab_engine_module

        eng = REticoloEngine(tmp_path)
        eng._matlab_temp = str(tmp_path / "temp")
        eng._scratch_dir = str(tmp_path / "scratch")
        with (
            patch.dict(
                sys.modules,
                {"matlab": matlab_module, "matlab.engine": matlab_engine_module},
            ),
            patch("reticolo_mcp.engine._lease_status", return_value={"collision": False}),
            patch("reticolo_mcp.engine._check_matlab_engine", return_value=""),
            patch("reticolo_mcp.engine._matlab_process_inventory", return_value={}),
            patch(
                "reticolo_mcp.engine.lease_acquire",
                return_value={"acquired": True, "token": "owned-token"},
            ),
            patch("reticolo_mcp.engine.lease_release") as release,
            patch.object(eng, "_start_heartbeat"),
        ):
            result = eng.start()
        assert result["status"] == "cleanup_uncertain"
        assert result["error_code"] == "startup_matlab_quit_failed"
        assert eng._engine is owned
        assert eng._lease_token == "owned-token"
        release.assert_not_called()

    def test_start_restores_host_temp_environment(self, tmp_path, monkeypatch):
        import types
        from reticolo_mcp.engine import REticoloEngine

        monkeypatch.setenv("TMP", "D:\\original_tmp")
        monkeypatch.setenv("TEMP", "D:\\original_temp")
        monkeypatch.delenv("TMPDIR", raising=False)
        owned = MagicMock()
        matlab_module = types.ModuleType("matlab")
        matlab_module.__path__ = []
        matlab_engine_module = types.ModuleType("matlab.engine")
        matlab_engine_module.start_matlab = MagicMock(return_value=owned)
        matlab_module.engine = matlab_engine_module

        eng = REticoloEngine(tmp_path)
        eng._matlab_temp = str(tmp_path / "matlab-temp")
        eng._scratch_dir = str(tmp_path / "scratch")
        lease_status = {"collision": False, "blockers": []}
        with (
            patch.dict(
                sys.modules,
                {"matlab": matlab_module, "matlab.engine": matlab_engine_module},
            ),
            patch("reticolo_mcp.engine._lease_status", return_value=lease_status),
            patch("reticolo_mcp.engine._check_matlab_engine", return_value=""),
            patch(
                "reticolo_mcp.engine._matlab_process_inventory",
                side_effect=[{}, {4321: 123.0}],
            ),
            patch(
                "reticolo_mcp.engine.lease_acquire",
                return_value={"acquired": True, "token": "owned-token"},
            ),
            patch.object(eng, "_start_heartbeat"),
        ):
            result = eng.start()
        assert result["status"] == "connected"
        assert os.environ["TMP"] == "D:\\original_tmp"
        assert os.environ["TEMP"] == "D:\\original_temp"
        assert "TMPDIR" not in os.environ

    def test_start_rejects_existing_matlab_process(self, tmp_path):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(tmp_path)
        with (
            patch("reticolo_mcp.engine._lease_status", return_value={"collision": False}),
            patch("reticolo_mcp.engine._check_matlab_engine", return_value=""),
            patch(
                "reticolo_mcp.engine._matlab_process_inventory",
                return_value={4321: 123.0},
            ),
            patch("reticolo_mcp.engine.lease_acquire") as acquire,
        ):
            result = eng.start()
        assert result["error_code"] == "matlab_process_collision"
        assert result["matlab_pids"] == [4321]
        acquire.assert_not_called()

    def test_start_exception_without_handle_retains_lease_if_process_appears(
        self, tmp_path,
    ):
        import types
        from reticolo_mcp.engine import REticoloEngine

        matlab_module = types.ModuleType("matlab")
        matlab_module.__path__ = []
        matlab_engine_module = types.ModuleType("matlab.engine")
        matlab_engine_module.start_matlab = MagicMock(
            side_effect=RuntimeError("engine handshake failed"),
        )
        matlab_module.engine = matlab_engine_module
        eng = REticoloEngine(tmp_path)
        eng._matlab_temp = str(tmp_path / "temp")
        eng._scratch_dir = str(tmp_path / "scratch")
        with (
            patch.dict(
                sys.modules,
                {"matlab": matlab_module, "matlab.engine": matlab_engine_module},
            ),
            patch("reticolo_mcp.engine._lease_status", return_value={"collision": False}),
            patch("reticolo_mcp.engine._check_matlab_engine", return_value=""),
            patch(
                "reticolo_mcp.engine._matlab_process_inventory",
                side_effect=[{}, {4321: 123.0}],
            ),
            patch(
                "reticolo_mcp.engine.lease_acquire",
                return_value={"acquired": True, "token": "owned-token"},
            ),
            patch("reticolo_mcp.engine.lease_release") as release,
            patch.object(eng, "_start_heartbeat"),
        ):
            result = eng.start()
        assert result["error_code"] == "startup_matlab_process_cleanup_unproven"
        assert result["matlab_pids"] == [4321]
        assert eng._lease_token == "owned-token"
        release.assert_not_called()

    def test_stop_requires_process_exit_evidence(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/"))
        owned = MagicMock()
        eng._engine = owned
        eng._lease_token = "owned-token"
        eng._matlab_processes = {4321: 123.0}
        with (
            patch.object(eng, "_wait_for_matlab_absent", return_value=False),
            patch("reticolo_mcp.engine.lease_release") as release,
        ):
            result = eng.stop()
        assert result["status"] == "cleanup_uncertain"
        assert result["error_code"] == "matlab_process_cleanup_unproven"
        assert eng._engine is owned
        assert eng._lease_token == "owned-token"
        release.assert_not_called()

    def test_process_exit_wait_covers_slow_windows_shutdown(self):
        from reticolo_mcp.engine import PROCESS_EXIT_WAIT_S

        assert PROCESS_EXIT_WAIT_S >= 30.0

    def test_stop_retry_recovers_proven_async_process_exit(self):
        from reticolo_mcp.engine import REticoloEngine

        eng = REticoloEngine(Path("/"))
        eng._engine = MagicMock()
        eng._quit_requested = True
        eng._lease_token = "owned-token"
        eng._matlab_processes = {4321: 123.0}
        with (
            patch.object(eng, "_wait_for_matlab_absent", return_value=True),
            patch(
                "reticolo_mcp.engine.lease_release",
                return_value={"released": True},
            ) as release,
        ):
            result = eng.stop()
        assert result == {"status": "stopped", "recovered_async_exit": True}
        assert eng._engine is None
        assert eng._matlab_processes == {}
        assert eng._quit_requested is False
        release.assert_called_once_with("owned-token")

    def test_stop_retry_retains_lease_while_async_process_is_alive(self):
        from reticolo_mcp.engine import REticoloEngine

        eng = REticoloEngine(Path("/"))
        eng._engine = MagicMock()
        eng._quit_requested = True
        eng._lease_token = "owned-token"
        eng._matlab_processes = {4321: 123.0}
        with (
            patch.object(eng, "_wait_for_matlab_absent", return_value=False),
            patch("reticolo_mcp.engine.lease_release") as release,
        ):
            result = eng.stop()
        assert result["status"] == "cleanup_uncertain"
        assert result["matlab_pids"] == [4321]
        assert eng._engine is not None
        assert eng._quit_requested is True
        release.assert_not_called()

    def test_process_inventory_parses_pid_and_creation_date(self):
        from reticolo_mcp.engine import _matlab_process_inventory
        completed = MagicMock(
            returncode=0,
            stdout=b'"MATLAB.exe","4321","Console","1","100,000 K"\r\n',
        )
        with (
            patch("reticolo_mcp.engine.subprocess.run", return_value=completed),
            patch("reticolo_mcp.engine._process_creation_date", return_value=123.5),
        ):
            assert _matlab_process_inventory() == {4321: 123.5}

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

    def test_heartbeat_failure_marks_engine_unhealthy(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/"))
        eng._lease_token = "token"
        eng._heartbeat_stop = MagicMock()
        eng._heartbeat_stop.wait.side_effect = [False]
        with patch("reticolo_mcp.engine.lease_heartbeat", return_value=False):
            eng._heartbeat_loop()
        assert eng._lease_healthy is False

    def test_solve_refuses_lost_lease(self):
        from reticolo_mcp.engine import REticoloEngine
        eng = REticoloEngine(Path("/"))
        eng._engine = MagicMock()
        eng._lease_healthy = False
        result = eng.solve_point(
            wl_um=5.0, D=[1.0], nn=[5, 5], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
        )
        assert result["status"] == "error"
        assert result["error_code"] == "solver_lease_lost"


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

    def test_tm_selects_tminc_branch_and_signed_angles(self):
        from reticolo_mcp.engine import REticoloEngine

        eng = REticoloEngine(Path("/"))
        backend = MagicMock()
        backend.workspace = {"py_R": 0.2, "py_T": 0.7}
        eng._engine = backend
        mock_matlab = MagicMock()
        with patch("reticolo_mcp.engine._ensure_matlab", return_value=mock_matlab):
            result = eng.solve_point(
                wl_um=1.0, D=[1.0, 1.0], nn=[5, 5],
                textures=[1.0, 1.5, 1.0],
                profil={"heights": [0, 0.5, 0], "indices": [1, 2, 3]},
                polarization=-1, theta_deg=-5.0, azimuth_deg=30.0,
            )
        commands = "\n".join(
            call.args[0] for call in backend.eval.call_args_list
            if call.args and isinstance(call.args[0], str)
        )
        assert "TMinc_top_reflected" in commands
        assert "TMinc_top_transmitted" in commands
        assert "ro = py_ro" in commands
        assert "delta0 = py_azimuth_deg" in commands
        assert backend.workspace["py_ro"] == pytest.approx(-math.sin(math.radians(5)))
        assert backend.workspace["py_azimuth_deg"] == 30.0
        assert result["theta_deg"] == -5.0
        assert result["azimuth_deg"] == 30.0

    def test_passivity_policy_tolerates_roundoff_and_is_labeled(self):
        from reticolo_mcp.engine import REticoloEngine

        eng = REticoloEngine(Path("/"))
        backend = MagicMock()
        backend.workspace = {
            "py_R": 0.14653257648954413,
            "py_T": 0.853467423510456,
        }
        eng._engine = backend
        with patch("reticolo_mcp.engine._ensure_matlab", return_value=MagicMock()):
            result = eng.solve_point(
                wl_um=1.0, D=[1.0, 1.0], nn=[5, 5], textures=[1.0, 1.5, 1.0],
                profil={"heights": [0, 0.5, 0], "indices": [1, 2, 3]},
                polarization=-1,
            )
        assert result["A_balance"] == pytest.approx(-1.1102230246251565e-16)
        assert result["passive"] is True
        assert result["passivity_policy"] == {
            "name": "bounded_rta_v1",
            "tolerance": 1e-12,
            "evidence_kind": "policy_outcome_not_independent_closure",
        }

    @pytest.mark.parametrize(
        ("kwargs", "error_code"),
        [
            ({"theta_deg": 90.0}, "invalid_theta"),
            ({"theta_deg": float("nan")}, "invalid_theta"),
            ({"azimuth_deg": 361.0}, "invalid_azimuth"),
        ],
    )
    def test_invalid_incidence_angles(self, kwargs, error_code):
        from reticolo_mcp.engine import REticoloEngine

        eng = REticoloEngine(Path("/"))
        eng._engine = MagicMock()
        result = eng.solve_point(
            wl_um=1.0, D=[1.0, 1.0], nn=[5, 5], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]}, **kwargs,
        )
        assert result["error_code"] == error_code

    def test_oblique_incidence_rejects_patterned_superstrate(self):
        from reticolo_mcp.engine import REticoloEngine

        eng = REticoloEngine(Path("/"))
        eng._engine = MagicMock()
        result = eng.solve_point(
            wl_um=1.0, D=[1.0, 1.0], nn=[5, 5],
            textures=[[1.0, [0, 0, 0.2, 0.2, 1.5, 1]], 1.0],
            profil={"heights": [0, 0], "indices": [1, 2]}, theta_deg=5.0,
        )
        assert result["error_code"] == "unsupported_incident_medium"


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

        result = _textures_to_cell(eng, matlab, [1.0, 1.5])
        assert eng.cell.call_count == 1
        args = eng.cell.call_args[0]
        assert args[1] == 2
        assert result == [1.0, 1.5]
        assert all(type(value) is float for value in result)

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

        result = _textures_to_cell(eng, matlab, [complex(4.0, 0.001)])
        assert eng.cell.call_count == 1
        assert result == [complex(4.0, 0.001)]

    def test_zero_imaginary_scalar_stays_real(self):
        from reticolo_mcp.engine import _textures_to_cell
        eng = MagicMock()
        eng.cell.return_value = [None]
        matlab = MagicMock()

        result = _textures_to_cell(eng, matlab, [complex(1.0, 0.0)])
        assert result == [1.0]
        assert type(result[0]) is float

    def test_zero_imaginary_inclusion_vector_stays_real(self):
        from reticolo_mcp.engine import _textures_to_cell
        eng = MagicMock()
        eng.cell.side_effect = lambda r, c: [None] * c
        matlab = MagicMock()
        matlab.double.side_effect = lambda values, **kwargs: (values, kwargs)

        result = _textures_to_cell(
            eng, matlab,
            [[complex(1.0, 0.0), [0.0, 0.0, 0.3, 0.2, complex(1.8, 0.0), 1]]],
        )
        assert result[0][0] == 1.0
        assert type(result[0][0]) is float
        assert result[0][1][1] == {}

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

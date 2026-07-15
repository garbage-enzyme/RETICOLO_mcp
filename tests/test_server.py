"""Unit tests for server input validation — no MATLAB required."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from reticolo_mcp import server
from reticolo_mcp.server import _validate_solve_inputs


class TestValidateSolveInputs:
    def _valid(self, **overrides):
        kwargs = {
            "wl_um": 5.0,
            "D": [1.0],
            "nn": [5, 5],
            "textures": [1.0],
            "profil": {"heights": [0.1, 0], "indices": [1, 1]},
            "polarization": 1,
            "config_id": "test",
        }
        kwargs.update(overrides)
        return _validate_solve_inputs(**kwargs)

    def test_valid_passes(self):
        assert self._valid() is None

    def test_wl_too_low(self):
        err = self._valid(wl_um=0.05)
        assert err is not None
        assert err["error_code"] == "invalid_wl"

    def test_wl_too_high(self):
        err = self._valid(wl_um=101.0)
        assert err is not None
        assert err["error_code"] == "invalid_wl"

    def test_D_empty(self):
        err = self._valid(D=[])
        assert err is not None
        assert err["error_code"] == "invalid_D"

    def test_D_three_entries(self):
        err = self._valid(D=[1.0, 2.0, 3.0])
        assert err is not None
        assert err["error_code"] == "invalid_D"

    def test_D_zero_period(self):
        err = self._valid(D=[0.0])
        assert err is not None
        assert err["error_code"] == "invalid_D"

    def test_D_negative_period(self):
        err = self._valid(D=[-1.0])
        assert err is not None
        assert err["error_code"] == "invalid_D"

    def test_rectangular_D_valid(self):
        assert self._valid(D=[1.0, 2.0]) is None

    def test_nn_wrong_length(self):
        err = self._valid(nn=[5])
        assert err is not None
        assert err["error_code"] == "invalid_nn"

    def test_nn_zero(self):
        err = self._valid(nn=[0, 5])
        assert err is not None
        assert err["error_code"] == "invalid_nn"

    def test_nn_negative(self):
        err = self._valid(nn=[-1, 5])
        assert err is not None
        assert err["error_code"] == "invalid_nn"

    def test_nn_not_int(self):
        err = self._valid(nn=[1.5, 5])
        assert err is not None
        assert err["error_code"] == "invalid_nn"

    def test_nn_over_hard_limit(self):
        err = self._valid(nn=[64, 5])
        assert err is not None
        assert err["error_code"] == "order_limit_exceeded"

    def test_invalid_polarization(self):
        err = self._valid(polarization=0)
        assert err is not None
        assert err["error_code"] == "invalid_polarization"

    def test_invalid_polarization_2(self):
        err = self._valid(polarization=2)
        assert err is not None
        assert err["error_code"] == "invalid_polarization"

    def test_tm_is_explicitly_unsupported(self):
        err = self._valid(polarization=-1)
        assert err is not None
        assert err["error_code"] == "unsupported_polarization"

    def test_config_id_too_long(self):
        from reticolo_mcp.config import MAX_CONFIG_ID_LEN
        err = self._valid(config_id="x" * (MAX_CONFIG_ID_LEN + 1))
        assert err is not None
        assert err["error_code"] == "config_id_too_long"

    def test_empty_heights(self):
        err = self._valid(profil={"heights": [], "indices": [1, 1]})
        assert err is not None
        assert err["error_code"] == "invalid_profil"

    def test_empty_indices(self):
        err = self._valid(profil={"heights": [0.1, 0], "indices": []})
        assert err is not None
        assert err["error_code"] == "invalid_profil"

    def test_last_height_not_zero(self):
        err = self._valid(profil={"heights": [0.1, 0.1], "indices": [1, 1]})
        assert err is not None
        assert err["error_code"] == "invalid_profil"

    def test_heights_indices_mismatch(self):
        err = self._valid(profil={"heights": [0.2, 0.1, 0], "indices": [1, 1]})
        assert err is not None
        assert err["error_code"] == "invalid_profil"

    def test_too_many_textures(self):
        from reticolo_mcp.config import MAX_TEXTURES
        err = self._valid(textures=[1.0] * (MAX_TEXTURES + 1))
        assert err is not None
        assert err["error_code"] == "too_many_textures"


class TestPublicJobControls:
    @pytest.fixture(autouse=True)
    def _isolated_runtime(self, tmp_path, monkeypatch):
        monkeypatch.setattr("reticolo_mcp.jobs.RUNTIME_DIR", tmp_path)

    def _resource_policy(self):
        return {
            "min_available_memory_fraction": 0.1,
            "warning_available_memory_fraction": 0.2,
            "min_commit_remaining_fraction": 0.1,
            "warning_commit_remaining_fraction": 0.2,
            "min_runtime_free_fraction": 0.1,
            "warning_runtime_free_fraction": 0.2,
            "max_points": 10,
            "wall_budget_s": 3600,
            "min_next_point_time_s": 60,
        }

    def _green_snapshot(self):
        from reticolo_mcp.resources import ResourceSnapshot
        return ResourceSnapshot(
            available_memory_fraction=0.5, commit_remaining_fraction=0.5,
            runtime_free_fraction=0.5, remaining_wall_s=3600,
        )

    @pytest.mark.parametrize("fn", [server.job_status, server.job_cancel, server.job_resume])
    def test_invalid_job_id_is_bounded_error(self, fn):
        result = fn("../escape")
        assert result == {"status": "error", "error_code": "invalid_job_id"}

    def test_invalid_tail_is_bounded_error(self):
        result = server.job_tail("job-abc", n="many")
        assert result == {"status": "error", "error_code": "invalid_tail"}

    def test_submit_validates_before_spawn(self, monkeypatch):
        spawn = MagicMock()
        monkeypatch.setattr(server, "_spawn_worker", spawn)
        result = server.job_submit(
            wls_um=[], D=[1.0], nn=[3, 3], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
        )
        assert result["error_code"] == "empty_job"
        spawn.assert_not_called()

    def test_submit_records_attempt_identity(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "_spawn_worker", lambda _job_id: 4321)
        monkeypatch.setattr(server, "sample_resources", lambda **_kwargs: self._green_snapshot())
        result = server.job_submit(
            wls_um=[1.0], D=[1.0], nn=[3, 3], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
            resource_policy=self._resource_policy(),
        )
        assert result["status"] == "ok"
        assert result["attempt_id"]
        state = server.jobs.read_state(result["job_id"])
        assert state["attempt"] == 1
        assert state["attempt_id"] == result["attempt_id"]

    def test_submit_requires_resource_policy(self, monkeypatch):
        spawn = MagicMock()
        monkeypatch.setattr(server, "_spawn_worker", spawn)
        result = server.job_submit(
            wls_um=[1.0], D=[1.0], nn=[3, 3], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
        )
        assert result["error_code"] == "resource_policy_required"
        spawn.assert_not_called()

    def test_resource_warning_requires_bound_confirmation(self, monkeypatch):
        from reticolo_mcp.resources import ResourceSnapshot
        monkeypatch.setattr(server, "sample_resources", lambda **_kwargs: ResourceSnapshot(
            available_memory_fraction=0.15, commit_remaining_fraction=0.5,
            runtime_free_fraction=0.5, remaining_wall_s=3600,
        ))
        result = server.job_submit(
            wls_um=[1.0], D=[1.0], nn=[3, 3], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
            resource_policy=self._resource_policy(),
        )
        assert result["error_code"] == "resource_warning_confirmation_required"
        assert len(result["resource_decision"]["decision_hash"]) == 64

    def test_spawn_worker_uses_hidden_window_flag(self, monkeypatch):
        popen = MagicMock()
        popen.return_value.pid = 9876
        monkeypatch.setattr(server.subprocess, "Popen", popen)
        monkeypatch.setattr(server.subprocess, "CREATE_NO_WINDOW", 0x08000000)
        assert server._spawn_worker("job-abc") == 9876
        assert popen.call_args.kwargs["creationflags"] == 0x08000000


class TestExperimentalGates:
    def test_synchronous_sweep_disabled_by_default(self):
        result = server.reticolo_sweep(
            wls_um=[5.0], D=[1.0], nn=[5, 5], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
            csv_path="D:\\outside-runtime.csv",
        )
        assert result["error_code"] == "experimental_tool_disabled"

    def test_convergence_disabled_by_default(self):
        result = server.reticolo_convergence(
            coarse_start=5.0, coarse_end=5.1, D=[1.0], nn=[5, 7],
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
        )
        assert result["error_code"] == "experimental_tool_disabled"

    def test_field_disabled_by_default(self):
        result = server.reticolo_field_export(
            wl_um=5.0, D=[1.0], nn=[5, 5], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
        )
        assert result["error_code"] == "experimental_tool_disabled"

    def test_enabled_field_still_validates_before_engine(self, monkeypatch):
        monkeypatch.setattr(server, "EXPERIMENTAL_ENABLED", True)
        result = server.reticolo_field_export(
            wl_um=5.0, D=[1.0], nn=[5, 5], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]}, component="bad",
        )
        assert result["error_code"] == "invalid_field_component"

    @pytest.mark.parametrize("step", [0.0, -0.1])
    def test_enabled_convergence_rejects_nonpositive_step(self, monkeypatch, step):
        monkeypatch.setattr(server, "EXPERIMENTAL_ENABLED", True)
        result = server.reticolo_convergence(
            coarse_start=5.0, coarse_end=5.1, coarse_step=step,
            D=[1.0], nn=[5, 7], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
        )
        assert result["error_code"] == "invalid_convergence_step"

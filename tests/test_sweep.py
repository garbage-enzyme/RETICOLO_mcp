"""Unit tests for resumable sweep — no MATLAB required."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reticolo_mcp.sweep import _read_completed, run_sweep


def _ok_result(wl: float) -> dict:
    return {
        "status": "ok", "wl_um": wl, "nn": [5, 5],
        "R": 0.1, "T": 0.8, "A": 0.1, "energy_sum": 1.0,
        "passive": True, "solve_time_s": 1.0, "config_id": "test",
    }


def _error_result(wl: float) -> dict:
    return {
        "status": "error", "wl_um": wl, "nn": [5, 5],
        "error": "fake error", "config_id": "test",
    }


class TestReadCompleted:
    def test_empty_csv(self, tmp_path):
        csv = tmp_path / "empty.csv"
        csv.write_text("wl_um,nn_x,nn_y,R,T,A,energy_sum,passive,solve_time_s,status,error,config_id,timestamp\n")
        assert _read_completed(csv, "test") == set()

    def test_skips_ok_rows(self, tmp_path):
        csv = tmp_path / "test.csv"
        csv.write_text(
            "wl_um,nn_x,nn_y,R,T,A,energy_sum,passive,solve_time_s,status,error,config_id,timestamp\n"
            "5.000,5,5,0.1,0.8,0.1,1.0,True,1.0,ok,,test,2026-07-13T00:00:00\n"
            "5.001,5,5,0.1,0.8,0.1,1.0,True,1.0,ok,,test,2026-07-13T00:00:00\n"
            "5.002,5,5,,,,,,,error,died,test,2026-07-13T00:00:00\n"
        )
        completed = _read_completed(csv, "test")
        assert completed == {5.000, 5.001}  # error row not skipped

    def test_different_config_not_skipped(self, tmp_path):
        csv = tmp_path / "test.csv"
        csv.write_text(
            "wl_um,nn_x,nn_y,R,T,A,energy_sum,passive,solve_time_s,status,error,config_id,timestamp\n"
            "5.000,5,5,0.1,0.8,0.1,1.0,True,1.0,ok,,old_config,2026-07-13T00:00:00\n"
        )
        assert _read_completed(csv, "new_config") == set()


class TestRunSweep:
    def test_all_skipped(self, tmp_path):
        engine = MagicMock()
        csv = tmp_path / "sweep.csv"
        csv.write_text(
            "wl_um,nn_x,nn_y,R,T,A,energy_sum,passive,solve_time_s,status,error,config_id,timestamp\n"
            "5.000,5,5,0.1,0.8,0.1,1.0,True,1.0,ok,,sweep1,2026-07-13T00:00:00\n"
        )
        r = run_sweep(
            engine, wls_um=[5.0], nn=[5, 5], D=1.0,
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
            config_id="sweep1", csv_path=str(csv),
        )
        assert r["solved"] == 0
        assert r["skipped"] == 1
        engine.solve_point.assert_not_called()

    def test_solves_pending(self, tmp_path):
        engine = MagicMock()
        engine.solve_point.side_effect = [_ok_result(5.0), _ok_result(5.1)]
        csv = tmp_path / "sweep.csv"

        r = run_sweep(
            engine, wls_um=[5.0, 5.1], nn=[5, 5], D=1.0,
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
            config_id="sweep2", csv_path=str(csv),
        )
        assert r["solved"] == 2
        assert r["errors"] == 0
        assert engine.solve_point.call_count == 2
        assert csv.exists()

    def test_mixed_ok_and_error(self, tmp_path):
        engine = MagicMock()
        engine.solve_point.side_effect = [_ok_result(5.0), _error_result(5.1)]
        csv = tmp_path / "sweep.csv"

        r = run_sweep(
            engine, wls_um=[5.0, 5.1], nn=[5, 5], D=1.0,
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
            config_id="sweep3", csv_path=str(csv),
        )
        assert r["solved"] == 1
        assert r["errors"] == 1
        assert r["status"] == "completed_with_errors"

    def test_resume_partial(self, tmp_path):
        engine = MagicMock()
        engine.solve_point.return_value = _ok_result(5.2)
        csv = tmp_path / "sweep.csv"
        csv.write_text(
            "wl_um,nn_x,nn_y,R,T,A,energy_sum,passive,solve_time_s,status,error,config_id,timestamp\n"
            "5.000,5,5,0.1,0.8,0.1,1.0,True,1.0,ok,,sweep4,2026-07-13T00:00:00\n"
            "5.100,5,5,,,,,,,error,crashed,sweep4,2026-07-13T00:00:00\n"
        )
        r = run_sweep(
            engine, wls_um=[5.0, 5.1, 5.2], nn=[5, 5], D=1.0,
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
            config_id="sweep4", csv_path=str(csv),
        )
        assert r["skipped"] == 1  # 5.0 is ok
        assert r["solved"] >= 1    # 5.1 (error row retried) + 5.2
        assert engine.solve_point.call_count >= 2

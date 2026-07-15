"""Unit tests for resumable sweep — no MATLAB required."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reticolo_mcp.sweep import _read_completed, _read_first_config_hash, analyze_sweep, run_sweep

HEADER = "wl_um,nn_x,nn_y,R,T,A_balance,passive,solve_time_s,status,error,config_hash,config_id,polarization,timestamp"


def _ok_result(wl: float) -> dict:
    return {
        "status": "ok", "wl_um": wl, "nn": [5, 5],
        "R": 0.1, "T": 0.8, "A_balance": 0.1,
        "passive": True, "solve_time_s": 1.0, "config_id": "test",
        "polarization": 1,
    }


def _error_result(wl: float) -> dict:
    return {
        "status": "error", "wl_um": wl, "nn": [5, 5],
        "error": "fake error", "config_id": "test",
    }


class TestReadCompleted:
    def test_empty_csv(self, tmp_path):
        csv = tmp_path / "empty.csv"
        csv.write_text(HEADER + "\n")
        assert _read_completed(csv, "test") == set()

    def test_skips_ok_rows(self, tmp_path):
        csv = tmp_path / "test.csv"
        csv.write_text(
            HEADER + "\n"
            "5.000,5,5,0.1,0.8,0.1,True,1.0,ok,,,test,1,2026-07-13T00:00:00\n"
            "5.001,5,5,0.1,0.8,0.1,True,1.0,ok,,,test,1,2026-07-13T00:00:00\n"
            "5.002,5,5,,,,,,,error,died,,test,,2026-07-13T00:00:00\n"
        )
        completed = _read_completed(csv, "test")
        assert completed == {5.000, 5.001}

    def test_different_config_not_skipped(self, tmp_path):
        csv = tmp_path / "test.csv"
        csv.write_text(
            HEADER + "\n"
            "5.000,5,5,0.1,0.8,0.1,True,1.0,ok,,,old_config,1,2026-07-13T00:00:00\n"
        )
        assert _read_completed(csv, "new_config") == set()


class TestRunSweep:
    def test_cancel_before_first_point(self, tmp_path):
        engine = MagicMock()
        csv_path = tmp_path / "sweep.csv"
        r = run_sweep(
            engine, wls_um=[5.0], nn=[5, 5], D=1.0,
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
            config_id="cancel-before", csv_path=str(csv_path),
            should_cancel=lambda: True,
        )
        assert r["status"] == "cancel_requested"
        assert r["cancel_observed"] is True
        assert r["solved"] == 0
        engine.solve_point.assert_not_called()

    def test_cancel_after_persisted_point(self, tmp_path):
        engine = MagicMock()
        engine.solve_point.side_effect = [_ok_result(5.0), _ok_result(5.1)]
        csv_path = tmp_path / "sweep.csv"

        r = run_sweep(
            engine, wls_um=[5.0, 5.1], nn=[5, 5], D=1.0,
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
            config_id="cancel-after", csv_path=str(csv_path),
            should_cancel=lambda: engine.solve_point.call_count >= 1,
        )

        assert r["status"] == "cancel_requested"
        assert r["cancel_observed"] is True
        assert r["solved"] == 1
        assert engine.solve_point.call_count == 1
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["status"] == "ok"

    def test_cancel_callback_error_fails_closed(self, tmp_path):
        engine = MagicMock()

        def broken_control():
            raise RuntimeError("control unavailable")

        r = run_sweep(
            engine, wls_um=[5.0], nn=[5, 5], D=1.0,
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
            config_id="cancel-error", csv_path=str(tmp_path / "sweep.csv"),
            should_cancel=broken_control,
        )
        assert r["status"] == "cancel_requested"
        engine.solve_point.assert_not_called()

    def test_all_skipped(self, tmp_path):
        engine = MagicMock()
        csv = tmp_path / "sweep.csv"
        csv.write_text(
            HEADER + "\n"
            "5.000,5,5,0.1,0.8,0.1,True,1.0,ok,,,sweep1,1,2026-07-13T00:00:00\n"
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
            HEADER + "\n"
            "5.000,5,5,0.1,0.8,0.1,True,1.0,ok,,,sweep4,1,2026-07-13T00:00:00\n"
            "5.100,5,5,,,,,,,error,crashed,,sweep4,,2026-07-13T00:00:00\n"
        )
        r = run_sweep(
            engine, wls_um=[5.0, 5.1, 5.2], nn=[5, 5], D=1.0,
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
            config_id="sweep4", csv_path=str(csv),
        )
        assert r["skipped"] == 1
        assert r["solved"] >= 1
        assert engine.solve_point.call_count >= 2


class TestReadFirstConfigHash:
    def test_returns_hash(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        csv_p.write_text(
            HEADER + "\n"
            "5.000,5,5,0.1,0.8,0.1,True,1.0,ok,,abc123,test,1,2026-07-13T00:00:00\n"
        )
        assert _read_first_config_hash(csv_p) == "abc123"

    def test_no_hash_column(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        csv_p.write_text("wl_um,status\n5.0,ok\n")
        assert _read_first_config_hash(csv_p) is None

    def test_empty_file(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        csv_p.write_text(HEADER + "\n")
        assert _read_first_config_hash(csv_p) is None

    def test_nonexistent_file(self, tmp_path):
        assert _read_first_config_hash(tmp_path / "none.csv") is None

    def test_empty_hash_field(self, tmp_path):
        csv_p = tmp_path / "test.csv"
        csv_p.write_text(
            HEADER + "\n"
            "5.000,5,5,0.1,0.8,0.1,True,1.0,ok,,,test,1,2026-07-13T00:00:00\n"
        )
        assert _read_first_config_hash(csv_p) is None


class TestAnalyzeSweep:
    def _write_csv(self, path: Path, rows: list[dict]) -> None:
        header = "wl_um,nn_x,nn_y,R,T,A_balance,passive,solve_time_s,status,error,config_hash,config_id,polarization,timestamp"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header.split(","))
            for r in rows:
                w.writerow([
                    f"{r['wl']:.6f}", "5", "5",
                    f"{r.get('R', 0.1):.12f}",
                    f"{r.get('T', 0.9 - r.get('A', 0.1)):.12f}",
                    f"{r.get('A', 0.1):.12f}",
                    "True", "1.0",
                    r.get("status", "ok"),
                    r.get("error", ""),
                    "",
                    "test", "1", "2026-07-13T00:00:00",
                ])

    def test_single_peak(self, tmp_path):
        p = tmp_path / "sweep.csv"
        self._write_csv(p, [
            {"wl": 5.0, "A": 0.1},
            {"wl": 5.1, "A": 0.5},
            {"wl": 5.2, "A": 0.9},
            {"wl": 5.3, "A": 0.5},
            {"wl": 5.4, "A": 0.1},
        ])
        result = analyze_sweep(p)
        assert result["points"] == 5
        assert len(result["peaks"]) == 1
        assert result["peaks"][0]["wl_um"] == 5.2
        assert result["peaks"][0]["A"] == 0.9
        assert not result["peaks"][0]["boundary"]
        assert len(result["boundary_maxima"]) == 0

    def test_boundary_maximum(self, tmp_path):
        p = tmp_path / "sweep.csv"
        self._write_csv(p, [
            {"wl": 5.0, "A": 0.9},
            {"wl": 5.1, "A": 0.5},
            {"wl": 5.2, "A": 0.3},
        ])
        result = analyze_sweep(p)
        assert len(result["peaks"]) == 0
        assert len(result["boundary_maxima"]) == 1
        assert result["boundary_maxima"][0]["wl_um"] == 5.0
        assert result["boundary_maxima"][0]["boundary"] is True

    def test_empty_csv(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text(HEADER + "\n")
        result = analyze_sweep(p)
        assert result["points"] == 0
        assert result["peaks"] == []

    def test_nonexistent_file(self, tmp_path):
        result = analyze_sweep(tmp_path / "none.csv")
        assert "error" in result

    def test_only_error_rows(self, tmp_path):
        p = tmp_path / "sweep.csv"
        self._write_csv(p, [
            {"wl": 5.0, "A": 0.1, "status": "error", "error": "fail"},
            {"wl": 5.1, "A": 0.5, "status": "error", "error": "fail"},
        ])
        result = analyze_sweep(p)
        assert result["points"] == 0

    def test_wl_range(self, tmp_path):
        p = tmp_path / "sweep.csv"
        self._write_csv(p, [
            {"wl": 5.0, "A": 0.1},
            {"wl": 5.5, "A": 0.2},
        ])
        result = analyze_sweep(p)
        assert result["wl_range"] == [5.0, 5.5]

    def test_mixed_ok_and_error_skip(self, tmp_path):
        p = tmp_path / "sweep.csv"
        self._write_csv(p, [
            {"wl": 5.0, "A": 0.1},
            {"wl": 5.1, "A": 0.5, "status": "error", "error": "oops"},
            {"wl": 5.2, "A": 0.3},
        ])
        result = analyze_sweep(p)
        assert result["points"] == 2

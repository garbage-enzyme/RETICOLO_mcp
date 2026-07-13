"""Unit tests for convergence helpers — no MATLAB required."""

from __future__ import annotations

import csv
from pathlib import Path

from reticolo_mcp.convergence import _arange, _estimate_fwhm


class TestArange:
    def test_basic(self):
        result = _arange(0, 1.0, 0.5)
        assert result == [0.0, 0.5, 1.0]

    def test_inclusive_end(self):
        result = _arange(0, 1.0, 0.3)
        assert result == [0.0, 0.3, 0.6, 0.9]

    def test_single_point(self):
        result = _arange(5.0, 5.0, 0.1)
        assert result == [5.0]

    def test_step_larger_than_range(self):
        result = _arange(0, 0.5, 1.0)
        assert result == [0.0, 1.0]

    def test_negative_range(self):
        result = _arange(-1.0, 0.0, 0.5)
        assert result == [-1.0, -0.5, 0.0]


class TestEstimateFWHM:
    def _write_csv(self, tmp_path: Path, data: list[tuple[float, float]]) -> Path:
        p = tmp_path / "fine.csv"
        header = "wl_um,nn_x,nn_y,R,T,A_balance,passive,solve_time_s,status,error,config_hash,config_id,polarization,timestamp"
        with open(p, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header.split(","))
            for wl, A in data:
                w.writerow([
                    f"{wl:.6f}", "5", "5",
                    f"{0.1:.12f}", f"{0.9 - A:.12f}", f"{A:.12f}",
                    "True", "1.0", "ok", "", "", "test", "1", "2026-07-13T00:00:00",
                ])
        return p

    def test_gaussian_like(self, tmp_path):
        data = [
            (5.280, 0.4),
            (5.285, 0.6),
            (5.290, 0.9),
            (5.292, 1.0),
            (5.294, 0.9),
            (5.296, 0.7),
            (5.300, 0.4),
        ]
        csv_path = self._write_csv(tmp_path, data)
        fwhm = _estimate_fwhm(csv_path)
        assert fwhm is not None
        assert 3 < fwhm < 25

    def test_too_few_points(self, tmp_path):
        data = [(5.290, 0.9), (5.292, 1.0)]
        csv_path = self._write_csv(tmp_path, data)
        assert _estimate_fwhm(csv_path) is None

    def test_zero_absorption(self, tmp_path):
        data = [(5.280, 0.0), (5.290, 0.0), (5.300, 0.0)]
        csv_path = self._write_csv(tmp_path, data)
        assert _estimate_fwhm(csv_path) is None

    def test_nonexistent_file(self, tmp_path):
        assert _estimate_fwhm(tmp_path / "nonexistent.csv") is None

    def test_missing_column(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text("wl_um,status\n5.0,ok\n")
        assert _estimate_fwhm(p) is None

    def test_no_ok_rows(self, tmp_path):
        p = tmp_path / "errors.csv"
        header = "wl_um,nn_x,nn_y,R,T,A_balance,passive,solve_time_s,status,error,config_hash,config_id,polarization,timestamp"
        with open(p, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header.split(","))
            w.writerow(["5.290", "5", "5", "", "", "", "", "1.0", "error", "fail", "", "t", "1", "2026-07-13T00:00:00"])
        assert _estimate_fwhm(p) is None

    def test_single_peak_narrow(self, tmp_path):
        data = [
            (5.290, 0.01),
            (5.291, 0.05),
            (5.292, 0.20),
            (5.293, 0.05),
            (5.294, 0.01),
        ]
        csv_path = self._write_csv(tmp_path, data)
        fwhm = _estimate_fwhm(csv_path)
        assert fwhm is not None
        assert 0.1 < fwhm < 10

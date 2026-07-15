"""Unit tests for convergence helpers — no MATLAB required."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

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

    def test_off_center_peak_uses_peak_not_row_midpoint(self, tmp_path):
        data = [
            (5.000, 0.05), (5.010, 0.50), (5.020, 1.00),
            (5.030, 0.50), (5.040, 0.05), (5.050, 0.02),
            (5.060, 0.01), (5.070, 0.01), (5.080, 0.01),
        ]
        fwhm = _estimate_fwhm(self._write_csv(tmp_path, data))
        assert fwhm == pytest.approx(20.0)

    def test_boundary_peak_is_unresolved(self, tmp_path):
        data = [(5.0, 1.0), (5.1, 0.5), (5.2, 0.1)]
        assert _estimate_fwhm(self._write_csv(tmp_path, data)) is None

    def test_missing_right_crossing_is_unresolved(self, tmp_path):
        data = [(5.0, 0.1), (5.1, 1.0), (5.2, 0.8), (5.3, 0.7)]
        assert _estimate_fwhm(self._write_csv(tmp_path, data)) is None

    def test_edge_min_baseline_is_explicit(self, tmp_path):
        data = [
            (5.00, 0.40), (5.01, 0.70), (5.02, 1.00),
            (5.03, 0.70), (5.04, 0.40),
        ]
        path = self._write_csv(tmp_path, data)
        assert _estimate_fwhm(
            path, baseline_rule="absolute_zero",
        ) == pytest.approx(33.3333333333)
        assert _estimate_fwhm(path, baseline_rule="edge_min") == pytest.approx(20.0)

    def test_unknown_baseline_rejected(self, tmp_path):
        path = self._write_csv(
            tmp_path, [(5.0, 0.1), (5.1, 1.0), (5.2, 0.1)],
        )
        with pytest.raises(ValueError, match="baseline_rule"):
            _estimate_fwhm(path, baseline_rule="invented")

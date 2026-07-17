"""Unit tests for field_export helpers — no MATLAB required."""

from __future__ import annotations

import pytest
import numpy as np
from unittest.mock import MagicMock

from reticolo_mcp.field_export import (
    _component_index,
    _plan_field_grid,
    _reshape_res3_field,
    _resolve_output_dir,
    _validate_field_request,
    _write_field_artifact,
    export_field,
)


class TestComponentIndex:
    def test_ex(self):
        assert _component_index("Ex") == 0

    def test_ey(self):
        assert _component_index("Ey") == 1

    def test_ez(self):
        assert _component_index("Ez") == 2

    def test_hx(self):
        assert _component_index("Hx") == 3

    def test_hy(self):
        assert _component_index("Hy") == 4

    def test_hz(self):
        assert _component_index("Hz") == 5

    def test_unknown_is_rejected(self):
        with pytest.raises(ValueError, match="unsupported"):
            _component_index("Unknown")

    def test_empty_string_is_rejected(self):
        with pytest.raises(ValueError, match="unsupported"):
            _component_index("")


class TestFieldRequestValidation:
    def _valid(self, **overrides):
        values = dict(
            wl_um=5.0, D=[1.0, 1.0], nn=[5, 5], component="normE",
            slice_axis="z", slice_value=0.0, slice_tol=1e-6,
            max_points=1000,
        )
        values.update(overrides)
        return _validate_field_request(**values)

    def test_valid(self):
        assert self._valid() is None

    @pytest.mark.parametrize("component", ["", "E", "Unknown"])
    def test_invalid_component(self, component):
        assert self._valid(component=component)["error_code"] == "invalid_field_component"

    def test_invalid_axis(self):
        assert self._valid(slice_axis="time")["error_code"] == "invalid_slice_axis"

    def test_tm_is_explicitly_unsupported(self):
        assert self._valid(polarization=-1)["error_code"] == "unsupported_polarization"

    @pytest.mark.parametrize("max_points", [0, -1, 500_001, True])
    def test_invalid_max_points(self, max_points):
        assert self._valid(max_points=max_points)["error_code"] == "invalid_max_points"

    def test_invalid_request_stops_before_engine_access(self):
        engine = MagicMock()
        result = export_field(
            engine, wl_um=5.0, D=[1.0, 1.0], nn=[5, 5], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
            component="bad",
        )
        assert result["error_code"] == "invalid_field_component"

    @pytest.mark.parametrize("value", [0, 202, True])
    def test_invalid_axis_point_count(self, value):
        assert self._valid(x_points=value)["error_code"] == "invalid_field_axis_points"

    @pytest.mark.parametrize("value", [0, 1, 202, True])
    def test_invalid_z_point_count(self, value):
        assert self._valid(z_points_per_layer=value)["error_code"] == (
            "invalid_field_z_points"
        )

    def test_field_requires_two_dimensional_period(self):
        assert self._valid(D=[1.0])["error_code"] == "invalid_field_geometry"

    def test_field_order_has_separate_internal_memory_cap(self):
        result = self._valid(nn=[16, 16])
        assert result["error_code"] == "field_order_limit_exceeded"
        assert result["hard_max_field_order"] == 15


class TestFieldGrid:
    def test_z_slice_uses_full_centered_xy_grid_and_bounded_estimate(self):
        x, y, estimate = _plan_field_grid(
            D=[2.0, 4.0],
            profil={"heights": [0.0, 1.0, 2.0, 0.0], "indices": [1, 2, 3, 1]},
            slice_axis="z", slice_value=1.0,
            x_points=3, y_points=5, z_points_per_layer=7,
        )
        assert x.tolist() == [-1.0, 0.0, 1.0]
        assert y.tolist() == [-2.0, -1.0, 0.0, 1.0, 2.0]
        assert estimate == 3 * 5 * (4 * 7 + 4 + 1)

    def test_x_slice_uses_single_requested_plane(self):
        x, y, _ = _plan_field_grid(
            D=[2.0, 4.0],
            profil={"heights": [0.0, 1.0, 0.0], "indices": [1, 2, 1]},
            slice_axis="x", slice_value=0.25,
            x_points=41, y_points=3, z_points_per_layer=5,
        )
        assert x.tolist() == [0.25]
        assert y.tolist() == [-2.0, 0.0, 2.0]

    def test_out_of_bounds_slice_is_rejected(self):
        with pytest.raises(ValueError, match="outside"):
            _plan_field_grid(
                D=[2.0, 4.0],
                profil={"heights": [0.0, 1.0, 0.0], "indices": [1, 2, 1]},
                slice_axis="x", slice_value=1.1,
                x_points=3, y_points=3, z_points_per_layer=5,
            )

    def test_point_estimate_refuses_before_engine_access(self):
        engine = MagicMock()
        engine._engine = MagicMock()
        result = export_field(
            engine, wl_um=5.0, D=[1.0, 1.0], nn=[5, 5], textures=[1.0],
            profil={"heights": [0.0, 1.0, 0.0], "indices": [1, 2, 1]},
            max_points=100, x_points=11, y_points=11, z_points_per_layer=5,
        )
        assert result["error_code"] == "field_point_estimate_exceeded"
        assert not engine._engine.eval.called

    def test_invalid_profile_is_rejected_before_engine_state(self):
        engine = MagicMock()
        result = export_field(
            engine, wl_um=5.0, D=[1.0, 1.0], nn=[5, 5], textures=[1.0],
            profil={"heights": [0.0, 1.0], "indices": [1]},
        )
        assert result["error_code"] == "invalid_field_grid"
        assert not engine._engine.eval.called


def test_res3_field_shape_is_restored_with_singleton_plane():
    values = np.zeros((2, 3, 6), dtype=complex)
    restored = _reshape_res3_field(values, nz=2, nx=1, ny=3)
    assert restored.shape == (2, 1, 3, 6)


def test_field_artifact_uses_generated_safe_name_and_hash(tmp_path):
    path, digest = _write_field_artifact(
        tmp_path, "field-safe123", x=np.array([0.0]), y=np.array([0.0]),
        z=np.array([0.0]), field=np.array([1.0 + 2.0j]),
    )
    assert path.parent == tmp_path
    assert path.name == "field-safe123.npz"
    assert len(digest) == 64
    assert not list(tmp_path.glob("*.tmp.npz"))


class TestArtifactPathPolicy:
    def test_child_path_is_allowed(self, tmp_path, monkeypatch):
        root = tmp_path / "artifacts"
        monkeypatch.setattr("reticolo_mcp.field_export.ARTIFACT_ROOT", root)
        assert _resolve_output_dir(root / "job-1") == (root / "job-1").resolve()

    def test_parent_escape_is_rejected(self, tmp_path, monkeypatch):
        root = tmp_path / "artifacts"
        monkeypatch.setattr("reticolo_mcp.field_export.ARTIFACT_ROOT", root)
        with pytest.raises(ValueError, match="inside"):
            _resolve_output_dir(root / ".." / "escape")

    def test_unsafe_path_rejected_before_engine_access(self, tmp_path, monkeypatch):
        root = tmp_path / "artifacts"
        monkeypatch.setattr("reticolo_mcp.field_export.ARTIFACT_ROOT", root)
        engine = MagicMock()
        result = export_field(
            engine, wl_um=5.0, D=[1.0, 1.0], nn=[5, 5], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
            output_dir=tmp_path / "outside",
        )
        assert result["error_code"] == "unsafe_output_path"

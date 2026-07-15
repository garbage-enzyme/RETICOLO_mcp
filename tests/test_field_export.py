"""Unit tests for field_export helpers — no MATLAB required."""

from __future__ import annotations

import pytest
import numpy as np
from unittest.mock import MagicMock

from reticolo_mcp.field_export import (
    _component_index,
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
            engine, wl_um=5.0, D=[1.0], nn=[5, 5], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
            component="bad",
        )
        assert result["error_code"] == "invalid_field_component"


def test_field_artifact_uses_generated_safe_name_and_hash(tmp_path):
    path, digest = _write_field_artifact(
        tmp_path, "field-safe123", x=np.array([0.0]), y=np.array([0.0]),
        z=np.array([0.0]), field=np.array([1.0 + 2.0j]),
    )
    assert path.parent == tmp_path
    assert path.name == "field-safe123.npz"
    assert len(digest) == 64
    assert not list(tmp_path.glob("*.tmp.npz"))

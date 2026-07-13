"""Unit tests for server input validation — no MATLAB required."""

from __future__ import annotations

import pytest

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

    def test_invalid_polarization(self):
        err = self._valid(polarization=0)
        assert err is not None
        assert err["error_code"] == "invalid_polarization"

    def test_invalid_polarization_2(self):
        err = self._valid(polarization=2)
        assert err is not None
        assert err["error_code"] == "invalid_polarization"

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

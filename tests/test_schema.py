"""Unit tests for Pydantic schema models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reticolo_mcp.schema import (
    ConstantEps,
    ConstantN,
    Drude,
    Excitation,
    Inclusion,
    Lattice,
    PatternedTexture,
    Profile,
    ProfileLayer,
    SolveSpec,
    UniformTexture,
)


def _valid_solve_payload() -> dict:
    return {
        "wl_um": 5.0,
        "lattice": {"px_um": 1.0, "py_um": 1.0},
        "nn": [5, 5],
        "materials": [{"type": "constant_n", "re": 1.0, "im": 0.0}],
        "textures": [{"material_id": 0}],
        "profile": {"layers": [{"material_id": 0, "thickness_um": 1.0}]},
    }


class TestStrictContracts:
    def test_unknown_field_rejected(self):
        payload = _valid_solve_payload()
        payload["unexpected"] = True
        with pytest.raises(ValidationError, match="extra_forbidden"):
            SolveSpec.model_validate(payload)

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
    def test_nonfinite_wavelength_rejected(self, value):
        payload = _valid_solve_payload()
        payload["wl_um"] = value
        with pytest.raises(ValidationError):
            SolveSpec.model_validate(payload)

    def test_missing_material_reference_rejected(self):
        payload = _valid_solve_payload()
        payload["textures"] = [{"material_id": 1}]
        with pytest.raises(ValidationError, match="missing material"):
            SolveSpec.model_validate(payload)

    def test_missing_texture_reference_rejected(self):
        payload = _valid_solve_payload()
        payload["profile"]["layers"][0]["material_id"] = 1
        with pytest.raises(ValidationError, match="missing texture"):
            SolveSpec.model_validate(payload)


class TestComplexMaterials:
    def test_constant_n(self):
        m = ConstantN(re=4.0, im=0.001)
        assert m.re == 4.0
        assert m.im == 0.001

    def test_constant_eps(self):
        m = ConstantEps(re=11.9, im=0)
        assert m.re == 11.9

    def test_drude(self):
        m = Drude(eps_inf=1.0, wp_rad_s=1.37e16, gamma_rad_s=4.08e13)
        assert m.wp_rad_s == 1.37e16

    def test_drude_rejects_negative_gamma(self):
        with pytest.raises(ValidationError):
            Drude(eps_inf=1, wp_rad_s=1e15, gamma_rad_s=-1)


class TestInclusion:
    def test_rectangle(self):
        inc = Inclusion(cx=0, cy=0, dx=0.3, dy=0.3, material_id=2)
        assert inc.n_slices == 1

    def test_rejects_zero_dimension(self):
        with pytest.raises(ValidationError):
            Inclusion(cx=0, cy=0, dx=0, dy=0.3, material_id=1)


class TestProfile:
    def test_simple_stack(self):
        p = Profile(layers=[
            ProfileLayer(material_id=0, thickness_um=0.1),
            ProfileLayer(material_id=1, thickness_um=0.4),
        ])
        h, idx = p.to_reticolo_format()
        assert h == [0.0, 0.1, 0.0]
        assert idx == [0, 0, 1]

    def test_empty_layers_rejected(self):
        with pytest.raises(ValidationError):
            Profile(layers=[])


class TestLattice:
    def test_square(self):
        L = Lattice(px_um=1.0)
        assert L.to_list() == [1.0]

    def test_rectangular(self):
        L = Lattice(px_um=4.0, py_um=2.0)
        assert L.to_list() == [4.0, 2.0]


class TestExcitation:
    def test_te_to_int(self):
        assert Excitation(polarization="TE").to_pol_int() == 1

    def test_tm_to_int(self):
        assert Excitation(polarization="TM").to_pol_int() == -1


class TestSolveSpec:
    def test_minimal_valid(self):
        spec = SolveSpec(
            wl_um=5.0,
            lattice=Lattice(px_um=1.0),
            nn=[5, 5],
            materials=[ConstantN(re=1.0), ConstantN(re=1.5)],
            textures=[UniformTexture(material_id=0), UniformTexture(material_id=1)],
            profile=Profile(layers=[
                ProfileLayer(material_id=0, thickness_um=0.1),
            ]),
        )
        assert spec.wl_um == 5.0

    def test_rejects_zero_wavelength(self):
        with pytest.raises(ValidationError):
            SolveSpec(
                wl_um=0,
                lattice=Lattice(px_um=1.0),
                nn=[5, 5],
                materials=[ConstantN(re=1.0)],
                textures=[UniformTexture(material_id=0)],
                profile=Profile(layers=[ProfileLayer(material_id=0, thickness_um=0.1)]),
            )

    def test_rejects_nn_zero(self):
        with pytest.raises(ValidationError):
            SolveSpec(
                wl_um=5.0,
                lattice=Lattice(px_um=1.0),
                nn=[0, 5],
                materials=[ConstantN(re=1.0)],
                textures=[UniformTexture(material_id=0)],
                profile=Profile(layers=[ProfileLayer(material_id=0, thickness_um=0.1)]),
            )

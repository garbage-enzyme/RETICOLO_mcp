"""Pydantic models for RETICOLO MCP — JSON-safe typed contracts.

All complex-valued materials use {re, im} pairs because JSON
has no native complex type. The engine modules translate these
internally to Python complex / MATLAB types.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class StrictModel(BaseModel):
    """Base for public contracts: reject unknown and non-finite input."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


# ------------------------------------------------------------------
# complex number — JSON-safe
# ------------------------------------------------------------------


class ComplexNumber(StrictModel):
    """Complex number as real/imaginary pair."""
    re: float
    im: float = 0.0

    def to_complex(self) -> complex:
        return complex(self.re, self.im)


# ------------------------------------------------------------------
# materials
# ------------------------------------------------------------------


class ConstantN(StrictModel):
    """Constant refractive index n + i*k."""
    type: Literal["constant_n"] = "constant_n"
    re: float
    im: float = 0.0


class ConstantEps(StrictModel):
    """Constant permittivity epsilon' + i*epsilon''."""
    type: Literal["constant_eps"] = "constant_eps"
    re: float
    im: float = 0.0


class Drude(StrictModel):
    """Drude model: eps_inf - wp^2 / (omega*(omega - i*gamma)).

    gamma > 0 for passive convention (exp(-i*omega*t)).
    """
    type: Literal["drude"] = "drude"
    eps_inf: float
    wp_rad_s: Annotated[float, Field(gt=0)]
    gamma_rad_s: Annotated[float, Field(gt=0)]


MaterialDef = ConstantN | ConstantEps | Drude


# ------------------------------------------------------------------
# geometry — inclusions, textures, profile
# ------------------------------------------------------------------


class Inclusion(StrictModel):
    """Rectangular/elliptical inclusion in a patterned layer.

    RETICOLO convention: [cx, cy, full_dx, full_dy, n, k]
    where k=1 for rectangle, >1 for ellipse approximation.
    """
    cx: float = 0.0
    cy: float = 0.0
    dx: Annotated[float, Field(gt=0)]
    dy: Annotated[float, Field(gt=0)]
    material_id: Annotated[int, Field(ge=0)]
    n_slices: Annotated[int, Field(ge=1)] = 1  # k in RETICOLO notation

    @field_validator("dx", "dy")
    @classmethod
    def positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"inclusion dimension must be >0, got {v}")
        return v


class UniformTexture(StrictModel):
    """Uniform layer: a single material."""
    material_id: Annotated[int, Field(ge=0)]


class PatternedTexture(StrictModel):
    """Patterned layer: background material + list of inclusions."""
    background_id: Annotated[int, Field(ge=0)]
    inclusions: list[Inclusion] = Field(default_factory=list, max_length=64)


TextureDef = UniformTexture | PatternedTexture


class ProfileLayer(StrictModel):
    """A single layer in the profile: texture ref + height in um."""
    material_id: Annotated[int, Field(ge=0)]
    thickness_um: Annotated[float, Field(gt=0)]


class Profile(StrictModel):
    """Layer stack profile, top to bottom. Last layer is semi-infinite substrate."""
    layers: Annotated[list[ProfileLayer], Field(min_length=1, max_length=32)]

    def to_reticolo_format(self) -> tuple[list[float], list[int]]:
        """Convert to RETICOLO {heights, indices} convention.

        heights: [0, h1, h1+h2, ..., 0]
          - First height=0 is the top interface.
          - Intermediate heights are cumulative z-positions downward.
          - Last height=0 marks the semi-infinite substrate below.
        indices: [i0, i1, ..., iN]
          - indices[0] is above heights[0] (semi-infinite superstrate).
          - indices[k] fills region between heights[k-1] and heights[k].
        """
        h = [0.0]
        cum = 0.0
        for layer in self.layers[:-1]:
            cum += layer.thickness_um
            h.append(cum)
        h.append(0.0)

        indices = [layer.material_id for layer in self.layers]
        # First index is superstrate (above top), repeat first material
        indices.insert(0, self.layers[0].material_id)

        return h, indices

    @model_validator(mode="after")
    def superstrate_substrate_order(self) -> "Profile":
        if len(self.layers) < 1:
            raise ValueError("at least one layer required")
        return self


# ------------------------------------------------------------------
# excitation
# ------------------------------------------------------------------


class Excitation(StrictModel):
    """Incident wave excitation parameters."""
    polarization: Literal["TE", "TM"] = "TE"
    theta_deg: Annotated[float, Field(ge=-90, le=90)] = 0.0
    phi_deg: Annotated[float, Field(ge=0, le=360)] = 0.0

    def to_pol_int(self) -> int:
        """RETICOLO parm.sym.pol convention."""
        return 1 if self.polarization == "TE" else -1


# ------------------------------------------------------------------
# lattice
# ------------------------------------------------------------------


class Lattice(StrictModel):
    """Lattice periods in um."""
    px_um: Annotated[float, Field(gt=0)]
    py_um: Annotated[float | None, Field(default=None, gt=0)] = None

    def to_list(self) -> list[float]:
        if self.py_um is not None:
            return [self.px_um, self.py_um]
        return [self.px_um]


# ------------------------------------------------------------------
# solve specification
# ------------------------------------------------------------------


class SolveSpec(StrictModel):
    """Complete specification for one RETICOLO solve point."""
    wl_um: Annotated[float, Field(gt=0)]
    lattice: Lattice
    nn: Annotated[list[int], Field(min_length=2, max_length=2)]
    materials: Annotated[list[MaterialDef], Field(min_length=1, max_length=32)]
    textures: Annotated[list[TextureDef], Field(min_length=1, max_length=32)]
    profile: Profile
    excitation: Excitation = Field(default_factory=Excitation)
    config_label: str = ""

    @field_validator("nn")
    @classmethod
    def odd_orders(cls, v: list[int]) -> list[int]:
        for n in v:
            if n < 1:
                raise ValueError(f"Fourier order must be >= 1, got {n}")
        return v

    @field_validator("wl_um")
    @classmethod
    def positive_wl(cls, v: float) -> float:
        if not (0.1 < v < 100.0):
            raise ValueError(f"wavelength out of range: {v}")
        return v

    @model_validator(mode="after")
    def validate_references(self) -> "SolveSpec":
        material_count = len(self.materials)
        for texture_index, texture in enumerate(self.textures):
            if isinstance(texture, UniformTexture):
                material_ids = [texture.material_id]
            else:
                material_ids = [texture.background_id]
                material_ids.extend(i.material_id for i in texture.inclusions)
            for material_id in material_ids:
                if material_id >= material_count:
                    raise ValueError(
                        f"texture {texture_index} references missing material "
                        f"{material_id}"
                    )

        texture_count = len(self.textures)
        for layer_index, layer in enumerate(self.profile.layers):
            if layer.material_id >= texture_count:
                raise ValueError(
                    f"profile layer {layer_index} references missing texture "
                    f"{layer.material_id}"
                )
        return self

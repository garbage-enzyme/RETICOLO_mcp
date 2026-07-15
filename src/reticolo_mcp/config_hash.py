"""Provenance-safe configuration identity via canonical hash.

Every physical configuration (materials, geometry, excitation, order)
produces a deterministic SHA-256 hash. Resume matches on this hash,
not on a user-provided label.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_config_hash(
    *,
    schema_version: str,
    reticolo_version: str,
    wls_um: list[float],
    D: list[float],
    nn: list[int],
    textures: list[Any],
    profil: dict[str, list],
    polarization: int,
    materials_spec: list[dict[str, Any]] | None = None,
) -> str:
    """Compute a deterministic SHA-256 hash over normalized solve inputs.

    Floats retain Python's round-trip representation; distinct finite inputs are
    never merged by decimal rounding. List order is preserved and dict keys sort.
    """
    D_norm = [float(v) for v in D]
    wls_norm = sorted([float(v) for v in wls_um])
    nn_norm = [int(v) for v in nn]

    heights = [float(v) for v in profil.get("heights", [])]
    indices = [int(v) for v in profil.get("indices", [])]

    payload = {
        "schema_version": schema_version,
        "reticolo_version": reticolo_version,
        "D": D_norm,
        "nn": nn_norm,
        "polarization": int(polarization),
        "wls_count": len(wls_norm),
        "wls_um": wls_norm,
        "textures_count": len(textures),
        "textures": normalize_textures(textures),
        "profil_heights": heights,
        "profil_indices": indices,
    }
    if materials_spec:
        payload["materials"] = _normalize_materials(materials_spec)

    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_textures(textures: list[Any]) -> list[Any]:
    """Normalize raw/JSON-safe RETICOLO textures without losing complex parts."""
    result: list[Any] = []
    for tex in textures:
        if _is_complex_value(tex):
            result.append(_complex_pair(tex))
            continue
        if not isinstance(tex, (list, tuple)) or not tex:
            raise ValueError("invalid texture")
        patterned: list[Any] = [_complex_pair(tex[0])]
        for inclusion in tex[1:]:
            if not isinstance(inclusion, (list, tuple)):
                raise ValueError("patterned texture inclusion must be a list")
            if len(inclusion) == 6:
                cx, cy, dx, dy, material, slices = inclusion
            elif len(inclusion) == 7:
                cx, cy, dx, dy, material_re, material_im, slices = inclusion
                material = [material_re, material_im]
            else:
                raise ValueError("inclusion must have 6 fields")
            patterned.append([
                float(cx), float(cy), float(dx), float(dy),
                _complex_pair(material), int(slices),
            ])
        result.append(patterned)
    return result


def _is_complex_value(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float, complex)):
        return True
    if isinstance(value, dict):
        return set(value) == {"re", "im"}
    return (
        isinstance(value, (list, tuple)) and len(value) == 2
        and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value)
    )


def _complex_pair(value: Any) -> list[float]:
    if isinstance(value, dict):
        c = complex(float(value["re"]), float(value["im"]))
    elif isinstance(value, (list, tuple)):
        c = complex(float(value[0]), float(value[1]))
    else:
        c = complex(value)
    return [float(c.real), float(c.imag)]


def _normalize_materials(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for m in materials:
        norm: dict[str, Any] = {}
        for k, v in sorted(m.items()):
            if isinstance(v, float):
                norm[k] = v
            elif isinstance(v, int):
                norm[k] = v
            else:
                norm[k] = str(v)
        result.append(norm)
    return result

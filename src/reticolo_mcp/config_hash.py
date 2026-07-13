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

    All float values are rounded to 9 decimal places before hashing.
    List/tuple order is preserved. Dict keys are sorted.
    """
    D_norm = [round(float(v), 9) for v in D]
    wls_norm = sorted([round(float(v), 9) for v in wls_um])
    nn_norm = [int(v) for v in nn]

    heights = [round(float(v), 9) for v in profil.get("heights", [])]
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
        "textures": _normalize_textures(textures),
        "profil_heights": heights,
        "profil_indices": indices,
    }
    if materials_spec:
        payload["materials"] = _normalize_materials(materials_spec)

    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_textures(textures: list[Any]) -> list[Any]:
    result = []
    for tex in textures:
        if isinstance(tex, (int, float, complex)):
            c = complex(tex)
            result.append([round(c.real, 9), round(c.imag, 9)])
        elif isinstance(tex, (list, tuple)):
            sub = []
            for item in tex:
                if isinstance(item, (int, float, complex)):
                    c = complex(item)
                    sub.append([round(c.real, 9), round(c.imag, 9)])
                elif isinstance(item, (list, tuple)):
                    sub.append([round(float(x), 9) for x in item])
                else:
                    sub.append(str(item))
            result.append(sub)
        else:
            result.append(str(tex))
    return result


def _normalize_materials(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for m in materials:
        norm: dict[str, Any] = {}
        for k, v in sorted(m.items()):
            if isinstance(v, float):
                norm[k] = round(v, 9)
            elif isinstance(v, int):
                norm[k] = v
            else:
                norm[k] = str(v)
        result.append(norm)
    return result

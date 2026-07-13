"""Field-data export for RETICOLO MCP.

Evaluates electromagnetic field components at mesh points,
filters to a slice plane, and exports bounded coordinate+value arrays.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .engine import _ensure_matlab, _textures_to_cell
import numpy as np


def export_field(
    engine: Any,
    *,
    wl_um: float,
    D: list[float],
    nn: list[int],
    textures: list[Any],
    profil: dict[str, list],
    polarization: int = 1,
    component: str = "normE",
    slice_axis: str = "z",
    slice_value: float = 0.0,
    slice_tol: float = 1e-6,
    max_points: int = 500_000,
    output_dir: str | Path | None = None,
    config_label: str = "",
) -> dict[str, Any]:
    """Solve at one wavelength with field computation enabled, export slice.

    Args:
        component: "Ex", "Ey", "Ez", "Hx", "Hy", "Hz", "normE", "normH".
        slice_axis: Axis to slice ("x", "y", or "z").
        slice_value: Coordinate value for the slice plane.
        max_points: Maximum mesh points rejected before evaluation.

    Returns:
        Summary with point count, coordinate bounds, max/min values,
        and export file paths.
    """
    if engine._engine is None:
        return {"status": "error", "error_code": "engine_not_started"}

    matlab = _ensure_matlab()
    t0 = time.time()

    try:
        eng = engine._engine

        eng.eval("parm.res1.champ = 1;", nargout=0)

        eng.workspace["py_wl"] = float(wl_um)
        eng.workspace["py_D"] = matlab.double(D)
        eng.workspace["py_nn"] = matlab.double([nn])
        eng.workspace["py_textures"] = _textures_to_cell(eng, matlab, textures)
        eng.workspace["py_heights"] = matlab.double(
            [float(v) for v in profil["heights"]])
        eng.workspace["py_indices"] = matlab.double(
            [[float(v) for v in profil["indices"]]])

        eng.eval(f"parm.sym.pol = {polarization};", nargout=0)

        eng.eval(
            "[py_aa, ~] = res1(py_wl, py_D, py_textures, py_nn, ro, delta0, parm);",
            nargout=0)
        eng.eval(
            "ef = res2(py_aa, {py_heights, py_indices});", nargout=0)
        eng.eval(
            "[py_e, py_o, py_x, py_y, py_z] = retchamp(ef);", nargout=0)

        e_raw = np.array(eng.workspace["py_e"], dtype=complex)
        x_raw = np.array(eng.workspace["py_x"], dtype=float)
        y_raw = np.array(eng.workspace["py_y"], dtype=float)
        z_raw = np.array(eng.workspace["py_z"], dtype=float)

        eng.eval("clear py_aa ef py_e py_o py_x py_y py_z;", nargout=0)
        eng.eval("parm.res1.champ = 0;", nargout=0)

    except Exception as exc:
        try:
            eng.eval("parm.res1.champ = 0;", nargout=0)
        except Exception:
            pass
        return {"status": "error", "error_code": "field_export_failed",
                "error": str(exc)[:500]}

    total_points = len(x_raw)
    if total_points > max_points:
        return {"status": "error", "error_code": "too_many_points",
                "total_points": total_points, "max_points": max_points}

    axis_idx = {"x": 0, "y": 1, "z": 2}[slice_axis]
    coords = [x_raw, y_raw, z_raw]
    coord_vals = coords[axis_idx]
    mask = np.abs(coord_vals - slice_value) < slice_tol

    if not mask.any():
        return {"status": "error", "error_code": "empty_slice",
                "slice_axis": slice_axis, "slice_value": slice_value,
                "coord_range": [float(coord_vals.min()), float(coord_vals.max())]}

    comp_idx = _component_index(component)
    field = e_raw[mask, comp_idx] if comp_idx < e_raw.shape[1] else np.abs(e_raw[mask, :]).max(axis=1) if component == "normE" else np.zeros(mask.sum())

    if component == "normE":
        field = np.sqrt(np.sum(np.abs(e_raw[mask, 0:3]) ** 2, axis=1))
    elif component == "normH":
        field = np.sqrt(np.sum(np.abs(e_raw[mask, 3:6]) ** 2, axis=1))

    slice_x = x_raw[mask]
    slice_y = y_raw[mask]
    slice_z = z_raw[mask]

    result = {
        "status": "ok",
        "wl_um": wl_um,
        "nn": nn,
        "component": component,
        "slice_axis": slice_axis,
        "slice_value": slice_value,
        "total_points": total_points,
        "slice_points": int(mask.sum()),
        "coord_bounds": {
            "x": [float(slice_x.min()), float(slice_x.max())],
            "y": [float(slice_y.min()), float(slice_y.max())],
            "z": [float(slice_z.min()), float(slice_z.max())],
        },
        "field_max": float(np.max(np.abs(field))),
        "field_min": float(np.min(np.abs(field))),
        "solve_time_s": round(time.time() - t0, 1),
    }

    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        npz_path = out / f"field_{config_label or 'export'}.npz"
        np.savez_compressed(
            npz_path,
            x=slice_x, y=slice_y, z=slice_z,
            field=np.abs(field),
            field_complex=field if np.any(np.iscomplex(field)) else np.abs(field),
        )
        summary_path = out / f"field_{config_label or 'export'}_summary.json"
        summary_path.write_text(json.dumps(result, indent=2, default=str))
        result["npz_path"] = str(npz_path)
        result["summary_path"] = str(summary_path)

    return result


def _component_index(name: str) -> int:
    """Map component name to RETICOLO e-field array index.

    RETICOLO e array order: [Ex, Ey, Ez, Hx, Hy, Hz]
    """
    mapping = {"Ex": 0, "Ey": 1, "Ez": 2, "Hx": 3, "Hy": 4, "Hz": 5}
    return mapping.get(name, 0)

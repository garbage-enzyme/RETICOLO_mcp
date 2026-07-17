"""Field-data export for RETICOLO MCP.

Evaluates electromagnetic field components at mesh points,
filters to a slice plane, and exports bounded coordinate+value arrays.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from .engine import _ensure_matlab, _textures_to_cell
from .config import ARTIFACT_ROOT
from .capabilities import _source_identity
from .config_hash import compute_config_hash
import numpy as np


ALLOWED_COMPONENTS = frozenset({"Ex", "Ey", "Ez", "Hx", "Hy", "Hz", "normE", "normH"})
ALLOWED_SLICE_AXES = frozenset({"x", "y", "z"})
HARD_MAX_FIELD_POINTS = 500_000
HARD_MAX_AXIS_POINTS = 201
HARD_MAX_Z_POINTS_PER_LAYER = 201
HARD_MAX_FIELD_ORDER = 15
HARD_MAX_FIELD_ARTIFACT_BYTES = 64 * 1024 * 1024
HARD_MAX_FIELD_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
HARD_MAX_FIELD_PAIR_COORDINATE_TOL_UM = 1e-6


def export_field(
    engine: Any,
    *,
    wl_um: float,
    D: list[float],
    nn: list[int],
    textures: list[Any],
    profil: dict[str, list],
    slice_tol: float,
    polarization: int = 1,
    component: str = "normE",
    slice_axis: str = "z",
    slice_value: float = 0.0,
    max_points: int = 500_000,
    x_points: int = 41,
    y_points: int = 41,
    z_points_per_layer: int = 21,
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
    validation_error = _validate_field_request(
        wl_um=wl_um, D=D, nn=nn, component=component,
        slice_axis=slice_axis, slice_value=slice_value,
        slice_tol=slice_tol, max_points=max_points, polarization=polarization,
        x_points=x_points, y_points=y_points,
        z_points_per_layer=z_points_per_layer,
    )
    if validation_error:
        return validation_error
    try:
        safe_output_dir = _resolve_output_dir(output_dir)
    except ValueError as exc:
        return {
            "status": "error", "error_code": "unsafe_output_path",
            "detail": str(exc),
        }
    try:
        x_axis, y_axis, estimated_points = _plan_field_grid(
            D=D,
            profil=profil,
            slice_axis=slice_axis,
            slice_value=slice_value,
            x_points=x_points,
            y_points=y_points,
            z_points_per_layer=z_points_per_layer,
        )
    except ValueError as exc:
        return {
            "status": "error", "error_code": "invalid_field_grid",
            "detail": str(exc),
        }
    if estimated_points > max_points:
        return {
            "status": "error", "error_code": "field_point_estimate_exceeded",
            "estimated_points": estimated_points, "max_points": max_points,
        }
    try:
        identities = _field_identities(
            reticolo_root=Path(engine._reticolo_dir),
            wl_um=wl_um,
            D=D,
            nn=nn,
            textures=textures,
            profil=profil,
            polarization=polarization,
            component=component,
            slice_axis=slice_axis,
            slice_value=slice_value,
            slice_tol=slice_tol,
            max_points=max_points,
            x_points=x_points,
            y_points=y_points,
            z_points_per_layer=z_points_per_layer,
        )
    except (OSError, ValueError) as exc:
        return {
            "status": "error", "error_code": "field_identity_failed",
            "detail": str(exc),
        }
    if engine._engine is None:
        return {"status": "error", "error_code": "engine_not_started"}

    matlab = _ensure_matlab()
    t0 = time.time()

    try:
        eng = engine._engine

        eng.eval(
            "parm.res1.champ=1; parm.res3.trace=0; "
            "parm.res3.cale=1:6; parm.res3.calo=[];",
            nargout=0,
        )

        eng.workspace["py_wl"] = float(wl_um)
        eng.workspace["py_D"] = matlab.double(D)
        eng.workspace["py_nn"] = matlab.double([nn])
        eng.workspace["py_textures"] = _textures_to_cell(eng, matlab, textures)
        eng.workspace["py_heights"] = matlab.double(
            [float(v) for v in profil["heights"]])
        eng.workspace["py_indices"] = matlab.double(
            [[float(v) for v in profil["indices"]]])
        eng.workspace["py_x_axis"] = matlab.double([x_axis.tolist()])
        eng.workspace["py_y_axis"] = matlab.double([y_axis.tolist()])
        eng.workspace["py_einc"] = matlab.double([[0.0, 1.0]])
        eng.workspace["py_z_points"] = float(z_points_per_layer)

        eng.eval(f"parm.sym.pol = {polarization};", nargout=0)
        eng.eval(
            "py_aa=res1(py_wl,py_D,py_textures,py_nn,ro,delta0,parm); "
            "parm.res3.npts=py_z_points; "
            "[py_e,py_z]=res3(py_x_axis,py_y_axis,py_aa,"
            "{py_heights,py_indices},py_einc,parm);",
            nargout=0,
        )

        e_raw = np.array(eng.workspace["py_e"], dtype=complex)
        z_raw = np.array(eng.workspace["py_z"], dtype=float)

        eng.eval(
            "clear py_aa py_e py_z py_x_axis py_y_axis py_einc py_z_points; "
            "parm.res1.champ=0;",
            nargout=0,
        )

    except Exception as exc:
        try:
            eng.eval(
                "clear py_aa py_e py_z py_x_axis py_y_axis py_einc py_z_points; "
                "parm.res1.champ=0;",
                nargout=0,
            )
        except Exception:
            pass
        return {"status": "error", "error_code": "field_export_failed",
                "error": str(exc)[:500]}

    z_axis = z_raw.reshape(-1)
    try:
        e_grid = _reshape_res3_field(
            e_raw, nz=len(z_axis), nx=len(x_axis), ny=len(y_axis),
        )
    except ValueError as exc:
        return {
            "status": "error", "error_code": "invalid_field_shape",
            "detail": str(exc),
        }
    z_grid, x_grid, y_grid = np.meshgrid(
        z_axis, x_axis, y_axis, indexing="ij",
    )
    x_raw = x_grid.reshape(-1)
    y_raw = y_grid.reshape(-1)
    z_raw = z_grid.reshape(-1)
    e_raw = e_grid.reshape(-1, e_grid.shape[-1])
    if e_raw.shape[1] < 6:
        return {"status": "error", "error_code": "invalid_field_shape"}
    total_points = len(x_raw)
    if not (len(y_raw) == len(z_raw) == total_points == e_raw.shape[0]):
        return {"status": "error", "error_code": "field_coordinate_shape_mismatch"}
    if not (
        np.isfinite(x_raw).all() and np.isfinite(y_raw).all()
        and np.isfinite(z_raw).all() and np.isfinite(e_raw.real).all()
        and np.isfinite(e_raw.imag).all()
    ):
        return {"status": "error", "error_code": "nonfinite_field_data"}
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

    if component == "normE":
        field = np.sqrt(np.sum(np.abs(e_raw[mask, 0:3]) ** 2, axis=1))
    elif component == "normH":
        field = np.sqrt(np.sum(np.abs(e_raw[mask, 3:6]) ** 2, axis=1))
    else:
        field = e_raw[mask, _component_index(component)]

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
        "estimated_points": estimated_points,
        "grid_shape": [len(z_axis), len(x_axis), len(y_axis)],
        "slice_points": int(mask.sum()),
        "coord_bounds": {
            "x": [float(slice_x.min()), float(slice_x.max())],
            "y": [float(slice_y.min()), float(slice_y.max())],
            "z": [float(slice_z.min()), float(slice_z.max())],
        },
        "field_max": float(np.max(np.abs(field))),
        "field_min": float(np.min(np.abs(field))),
        "solve_time_s": round(time.time() - t0, 1),
        **identities,
    }

    if safe_output_dir is not None:
        out = safe_output_dir
        out.mkdir(parents=True, exist_ok=True)
        artifact_id = f"field-{uuid.uuid4().hex[:16]}"
        npz_path, npz_hash = _write_field_artifact(
            out, artifact_id, x=slice_x, y=slice_y, z=slice_z, field=field,
            identities=identities,
        )
        result["artifact_id"] = artifact_id
        result["artifact_sha256"] = npz_hash
        result["visual_review_state"] = "visual_review_required"
        result["npz_path"] = str(npz_path)
        summary_path = out / f"{artifact_id}_summary.json"
        result["summary_path"] = str(summary_path)
        _atomic_write_json(summary_path, result)

    return result


def _component_index(name: str) -> int:
    """Map component name to RETICOLO e-field array index.

    RETICOLO e array order: [Ex, Ey, Ez, Hx, Hy, Hz]
    """
    mapping = {"Ex": 0, "Ey": 1, "Ez": 2, "Hx": 3, "Hy": 4, "Hz": 5}
    if name not in mapping:
        raise ValueError(f"unsupported field component: {name}")
    return mapping[name]


def _validate_field_request(
    *, wl_um: float, D: list[float], nn: list[int], component: str,
    slice_axis: str, slice_value: float, slice_tol: float, max_points: int,
    polarization: int = 1,
    x_points: int = 41, y_points: int = 41, z_points_per_layer: int = 21,
) -> dict[str, Any] | None:
    if (
        isinstance(slice_tol, bool)
        or not isinstance(slice_tol, (int, float))
        or not math.isfinite(float(slice_tol))
    ):
        return {"status": "error", "error_code": "invalid_slice_tolerance"}
    values = [wl_um, slice_value, slice_tol, *D]
    try:
        finite = all(math.isfinite(float(v)) for v in values)
    except (TypeError, ValueError):
        finite = False
    if not finite:
        return {"status": "error", "error_code": "nonfinite_field_request"}
    if float(wl_um) <= 0 or len(D) != 2 or any(float(v) <= 0 for v in D):
        return {"status": "error", "error_code": "invalid_field_geometry"}
    if len(nn) != 2 or any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in nn):
        return {"status": "error", "error_code": "invalid_field_order"}
    if any(value > HARD_MAX_FIELD_ORDER for value in nn):
        return {
            "status": "error", "error_code": "field_order_limit_exceeded",
            "hard_max_field_order": HARD_MAX_FIELD_ORDER,
        }
    if component not in ALLOWED_COMPONENTS:
        return {"status": "error", "error_code": "invalid_field_component"}
    if slice_axis not in ALLOWED_SLICE_AXES:
        return {"status": "error", "error_code": "invalid_slice_axis"}
    if slice_tol <= 0:
        return {"status": "error", "error_code": "invalid_slice_tolerance"}
    if isinstance(max_points, bool) or not isinstance(max_points, int) or not (
        1 <= max_points <= HARD_MAX_FIELD_POINTS
    ):
        return {
            "status": "error", "error_code": "invalid_max_points",
            "hard_max_points": HARD_MAX_FIELD_POINTS,
        }
    axis_counts = (x_points, y_points)
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        or not 1 <= value <= HARD_MAX_AXIS_POINTS
        for value in axis_counts
    ):
        return {
            "status": "error", "error_code": "invalid_field_axis_points",
            "hard_max_axis_points": HARD_MAX_AXIS_POINTS,
        }
    if (
        isinstance(z_points_per_layer, bool)
        or not isinstance(z_points_per_layer, int)
        or not 2 <= z_points_per_layer <= HARD_MAX_Z_POINTS_PER_LAYER
    ):
        return {
            "status": "error", "error_code": "invalid_field_z_points",
            "hard_max_z_points_per_layer": HARD_MAX_Z_POINTS_PER_LAYER,
        }
    if polarization == -1:
        return {"status": "error", "error_code": "unsupported_polarization"}
    if polarization != 1:
        return {"status": "error", "error_code": "invalid_polarization"}
    return None


def _plan_field_grid(
    *,
    D: list[float],
    profil: dict[str, list],
    slice_axis: str,
    slice_value: float,
    x_points: int,
    y_points: int,
    z_points_per_layer: int,
) -> tuple[np.ndarray, np.ndarray, int]:
    heights = profil.get("heights")
    indices = profil.get("indices")
    if (
        not isinstance(heights, list) or not isinstance(indices, list)
        or not heights or len(heights) != len(indices)
    ):
        raise ValueError("profil heights and indices must be nonempty equal-length lists")
    try:
        height_values = [float(value) for value in heights]
        index_values = [float(value) for value in indices]
    except (TypeError, ValueError) as exc:
        raise ValueError("profil values must be numeric") from exc
    if not all(math.isfinite(value) for value in (*height_values, *index_values)):
        raise ValueError("profil values must be finite")
    if any(value < 0 for value in height_values):
        raise ValueError("profil heights must be nonnegative")
    if any(value < 1 or not value.is_integer() for value in index_values):
        raise ValueError("profil indices must be positive integers")

    x_bounds = (-float(D[0]) / 2.0, float(D[0]) / 2.0)
    y_bounds = (-float(D[1]) / 2.0, float(D[1]) / 2.0)
    z_bounds = (0.0, sum(height_values))
    bounds = {"x": x_bounds, "y": y_bounds, "z": z_bounds}[slice_axis]
    if not bounds[0] <= slice_value <= bounds[1]:
        raise ValueError(f"slice_value is outside the {slice_axis} field bounds")

    x_axis = (
        np.array([slice_value], dtype=float)
        if slice_axis == "x"
        else np.linspace(*x_bounds, num=x_points, dtype=float)
    )
    y_axis = (
        np.array([slice_value], dtype=float)
        if slice_axis == "y"
        else np.linspace(*y_bounds, num=y_points, dtype=float)
    )
    estimated_z = len(height_values) * z_points_per_layer + len(height_values) + 1
    estimated_points = len(x_axis) * len(y_axis) * estimated_z
    return x_axis, y_axis, estimated_points


def _reshape_res3_field(
    values: np.ndarray, *, nz: int, nx: int, ny: int,
) -> np.ndarray:
    array = np.asarray(values, dtype=complex)
    if array.ndim < 2 or array.shape[-1] < 6:
        raise ValueError(f"res3 field has unsupported shape {array.shape}")
    components = array.shape[-1]
    expected_values = nz * nx * ny * components
    if array.size != expected_values:
        raise ValueError(
            f"res3 field shape {array.shape} does not match grid {(nz, nx, ny)}"
        )
    return array.reshape(nz, nx, ny, components)


def _field_identities(
    *,
    reticolo_root: Path,
    wl_um: float,
    D: list[float],
    nn: list[int],
    textures: list[Any],
    profil: dict[str, list],
    polarization: int,
    component: str,
    slice_axis: str,
    slice_value: float,
    slice_tol: float,
    max_points: int,
    x_points: int,
    y_points: int,
    z_points_per_layer: int,
) -> dict[str, str]:
    reticolo_source_sha256 = _reticolo_source_identity(reticolo_root)
    physical_config_sha256 = compute_config_hash(
        schema_version="reticolo_field_physical/1",
        reticolo_version=reticolo_source_sha256,
        wls_um=[wl_um],
        D=D,
        nn=nn,
        textures=textures,
        profil=profil,
        polarization=polarization,
    )
    pairing_config_sha256 = compute_config_hash(
        schema_version="reticolo_field_pairing_physical/1",
        reticolo_version=reticolo_source_sha256,
        wls_um=[],
        D=D,
        nn=nn,
        textures=textures,
        profil=profil,
        polarization=polarization,
    )
    point_fingerprint_sha256 = _canonical_sha256({
        "schema": "reticolo_field_point/1",
        "physical_config_sha256": physical_config_sha256,
        "wl_um": float(wl_um),
        "nn": [int(value) for value in nn],
        "polarization": int(polarization),
    })
    sampling_payload = {
        "schema": "reticolo_field_sampling/1",
        "component": component,
        "slice_axis": slice_axis,
        "slice_value": float(slice_value),
        "slice_tol": float(slice_tol),
        "max_points": max_points,
        "x_points": x_points,
        "y_points": y_points,
        "z_points_per_layer": z_points_per_layer,
    }
    field_sampling_sha256 = _canonical_sha256(sampling_payload)
    field_request_sha256 = _canonical_sha256({
        "schema": "reticolo_field_request/1",
        "point_fingerprint_sha256": point_fingerprint_sha256,
        "field_sampling_sha256": field_sampling_sha256,
    })
    return {
        "field_schema": "reticolo_field_artifact/1",
        "collector_source_sha256": _source_identity(),
        "reticolo_source_sha256": reticolo_source_sha256,
        "physical_config_sha256": physical_config_sha256,
        "pairing_config_sha256": pairing_config_sha256,
        "point_fingerprint_sha256": point_fingerprint_sha256,
        "field_sampling_sha256": field_sampling_sha256,
        "field_request_sha256": field_request_sha256,
    }


def _reticolo_source_identity(root: Path) -> str:
    resolved = root.resolve(strict=True)
    paths = sorted(resolved.glob("*.m"), key=lambda path: path.name.casefold())
    if not paths:
        raise ValueError("RETICOLO source root has no .m files")
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def assemble_field_pair(
    *,
    on_artifact: str | Path,
    off_artifact: str | Path,
    on_sha256: str,
    off_sha256: str,
    coordinate_tolerance_um: float,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Validate two field artifacts and emit a bounded shared-grid pair."""
    if (
        isinstance(coordinate_tolerance_um, bool)
        or not isinstance(coordinate_tolerance_um, (int, float))
        or not math.isfinite(float(coordinate_tolerance_um))
        or not 0 <= float(coordinate_tolerance_um) <= HARD_MAX_FIELD_PAIR_COORDINATE_TOL_UM
    ):
        return {
            "status": "error",
            "error_code": "invalid_field_pair_coordinate_tolerance",
            "hard_max_coordinate_tolerance_um": HARD_MAX_FIELD_PAIR_COORDINATE_TOL_UM,
        }
    coordinate_tolerance_um = float(coordinate_tolerance_um)
    try:
        out = _resolve_output_dir(output_dir)
        if out is None:
            raise ValueError("output_dir is required")
        on_path = _resolve_input_artifact(on_artifact)
        off_path = _resolve_input_artifact(off_artifact)
    except ValueError as exc:
        return {"status": "error", "error_code": "unsafe_field_pair_path", "detail": str(exc)}
    if on_path == off_path:
        return {"status": "error", "error_code": "field_pair_requires_distinct_artifacts"}
    actual_hashes = {"on": _sha256_path(on_path), "off": _sha256_path(off_path)}
    if actual_hashes != {"on": on_sha256, "off": off_sha256}:
        return {
            "status": "error", "error_code": "field_pair_hash_mismatch",
            "actual_sha256": actual_hashes,
        }
    try:
        on = _load_field_artifact(on_path)
        off = _load_field_artifact(off_path)
    except (OSError, ValueError) as exc:
        return {"status": "error", "error_code": "invalid_field_pair_artifact", "detail": str(exc)[:500]}

    equal_metadata = (
        "field_schema", "collector_source_sha256", "reticolo_source_sha256",
        "pairing_config_sha256", "field_sampling_sha256",
    )
    mismatched = [name for name in equal_metadata if on[name] != off[name]]
    if mismatched:
        return {
            "status": "error", "error_code": "incompatible_field_pair_metadata",
            "mismatched": mismatched,
        }
    if on["point_fingerprint_sha256"] == off["point_fingerprint_sha256"]:
        return {"status": "error", "error_code": "field_pair_points_not_distinct"}
    coordinate_names = ("x", "y", "z")
    if any(on[name].shape != off[name].shape for name in coordinate_names):
        return {"status": "error", "error_code": "field_pair_grid_shape_mismatch"}
    coordinate_max_delta_um = {
        name: float(np.max(np.abs(on[name] - off[name])))
        for name in coordinate_names
    }
    if any(
        delta > coordinate_tolerance_um
        for delta in coordinate_max_delta_um.values()
    ):
        return {
            "status": "error", "error_code": "field_pair_grid_mismatch",
            "coordinate_max_delta_um": coordinate_max_delta_um,
            "coordinate_tolerance_um": coordinate_tolerance_um,
        }

    on_field = on["field"]
    off_field = off["field"]
    if on_field.shape != off_field.shape or on_field.ndim != 1 or not on_field.size:
        return {"status": "error", "error_code": "field_pair_value_shape_mismatch"}
    if not (
        np.isfinite(on_field).all() and np.isfinite(off_field).all()
        and np.all(on_field >= 0) and np.all(off_field >= 0)
    ):
        return {"status": "error", "error_code": "field_pair_nonfinite_values"}

    shared_min = float(min(np.min(on_field), np.min(off_field)))
    shared_max = float(max(np.max(on_field), np.max(off_field)))
    off_max = float(np.max(off_field))
    off_mean_square = float(np.mean(np.square(off_field)))
    pair_id = f"field-pair-{uuid.uuid4().hex[:16]}"
    out.mkdir(parents=True, exist_ok=True)
    final_path = out / f"{pair_id}.npz"
    temp_path = out / f".{pair_id}.{uuid.uuid4().hex[:8]}.tmp.npz"
    try:
        np.savez_compressed(
            temp_path,
            x=on["x"], y=on["y"], z=on["z"],
            on_field=on_field, off_field=off_field,
            on_field_complex=on["field_complex"],
            off_field_complex=off["field_complex"],
            shared_vmin=np.array(shared_min), shared_vmax=np.array(shared_max),
            on_artifact_sha256=np.array(on_sha256), off_artifact_sha256=np.array(off_sha256),
            pairing_config_sha256=np.array(on["pairing_config_sha256"]),
            field_sampling_sha256=np.array(on["field_sampling_sha256"]),
            on_point_fingerprint_sha256=np.array(on["point_fingerprint_sha256"]),
            off_point_fingerprint_sha256=np.array(off["point_fingerprint_sha256"]),
            coordinate_tolerance_um=np.array(coordinate_tolerance_um),
        )
        with temp_path.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_path, final_path)
    finally:
        temp_path.unlink(missing_ok=True)

    result = {
        "status": "ok",
        "pair_schema": "reticolo_field_pair/1",
        "pair_id": pair_id,
        "artifact_path": str(final_path),
        "artifact_sha256": _sha256_path(final_path),
        "on_artifact_sha256": on_sha256,
        "off_artifact_sha256": off_sha256,
        "pairing_config_sha256": on["pairing_config_sha256"],
        "field_sampling_sha256": on["field_sampling_sha256"],
        "on_point_fingerprint_sha256": on["point_fingerprint_sha256"],
        "off_point_fingerprint_sha256": off["point_fingerprint_sha256"],
        "point_count": int(on_field.size),
        "coordinate_max_delta_um": coordinate_max_delta_um,
        "coordinate_tolerance_um": coordinate_tolerance_um,
        "shared_limits": [shared_min, shared_max],
        "max_abs_ratio_on_over_off": (
            float(np.max(on_field)) / off_max if off_max > 0 else None
        ),
        "mean_square_ratio_on_over_off": (
            float(np.mean(np.square(on_field))) / off_mean_square
            if off_mean_square > 0 else None
        ),
        "visual_review_state": "visual_review_required",
        "claim_scope": "numerical_pair_only_no_mode_classification",
    }
    summary_path = out / f"{pair_id}_summary.json"
    result["summary_path"] = str(summary_path)
    _atomic_write_json(summary_path, result)
    return result


def _resolve_input_artifact(path: str | Path) -> Path:
    root = ARTIFACT_ROOT.resolve(strict=True)
    candidate = Path(path).resolve(strict=True)
    if candidate.parent != root and root not in candidate.parents:
        raise ValueError("input artifact must stay inside RETICOLO_ARTIFACT_DIR")
    if candidate.suffix.casefold() != ".npz" or not candidate.is_file():
        raise ValueError("input artifact must be an existing NPZ file")
    if candidate.stat().st_size > HARD_MAX_FIELD_ARTIFACT_BYTES:
        raise ValueError("input field artifact exceeds the hard size cap")
    return candidate


def _load_field_artifact(path: Path) -> dict[str, Any]:
    array_names = {"x", "y", "z", "field", "field_complex"}
    metadata_names = {
        "field_schema", "collector_source_sha256", "reticolo_source_sha256",
        "physical_config_sha256", "pairing_config_sha256",
        "point_fingerprint_sha256", "field_sampling_sha256", "field_request_sha256",
    }
    try:
        with zipfile.ZipFile(path) as zipped:
            if sum(info.file_size for info in zipped.infolist()) > HARD_MAX_FIELD_UNCOMPRESSED_BYTES:
                raise ValueError("field artifact exceeds the uncompressed size cap")
    except zipfile.BadZipFile as exc:
        raise ValueError("field artifact is not a valid NPZ archive") from exc
    with np.load(path, allow_pickle=False) as archive:
        missing = sorted((array_names | metadata_names) - set(archive.files))
        if missing:
            raise ValueError(f"field artifact is missing entries {missing}")
        result = {name: np.array(archive[name]) for name in array_names}
        for name in metadata_names:
            value = archive[name]
            if value.shape != ():
                raise ValueError(f"field metadata {name} must be scalar")
            result[name] = str(value.item())
    lengths = {result[name].size for name in array_names}
    if len(lengths) != 1:
        raise ValueError("field artifact arrays have inconsistent lengths")
    point_count = next(iter(lengths))
    if not 1 <= point_count <= HARD_MAX_FIELD_POINTS:
        raise ValueError("field artifact point count exceeds the hard bound")
    if any(result[name].ndim != 1 for name in array_names):
        raise ValueError("field artifact arrays must be one-dimensional")
    if not all(
        np.isfinite(result[name].real).all() and np.isfinite(result[name].imag).all()
        for name in array_names
    ):
        raise ValueError("field artifact arrays must be finite")
    if np.any(result["field"] < 0) or not np.array_equal(
        result["field"], np.abs(result["field_complex"]), equal_nan=False,
    ):
        raise ValueError("field magnitude is inconsistent with field_complex")
    return result


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_output_dir(output_dir: str | Path | None) -> Path | None:
    if output_dir is None or str(output_dir) == "":
        return None
    root = ARTIFACT_ROOT.resolve()
    candidate = Path(output_dir).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("output_dir must stay inside RETICOLO_ARTIFACT_DIR")
    if len(str(candidate)) > 240:
        raise ValueError("output_dir exceeds safe Windows path length")
    return candidate


def _write_field_artifact(
    output_dir: Path, artifact_id: str, *, x: np.ndarray, y: np.ndarray,
    z: np.ndarray, field: np.ndarray, identities: dict[str, str],
) -> tuple[Path, str]:
    final_path = output_dir / f"{artifact_id}.npz"
    temp_path = output_dir / f".{artifact_id}.{uuid.uuid4().hex[:8]}.tmp.npz"
    try:
        np.savez_compressed(
            temp_path, x=x, y=y, z=z, field=np.abs(field),
            field_complex=field if np.iscomplexobj(field) else np.abs(field),
            **{key: np.array(value) for key, value in identities.items()},
        )
        with open(temp_path, "r+b") as f:
            os.fsync(f.fileno())
        os.replace(temp_path, final_path)
    finally:
        temp_path.unlink(missing_ok=True)
    digest = hashlib.sha256(final_path.read_bytes()).hexdigest()
    return final_path, digest


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(temp_path, "x", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)

"""Bounded durable filesystem primitives for Windows runtime artifacts."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Callable


DEFAULT_RETRY_ATTEMPTS = 6
DEFAULT_RETRY_DELAY_S = 0.02


def atomic_write_bytes(
    path: Path, payload: bytes, *, attempts: int = DEFAULT_RETRY_ATTEMPTS,
    delay_s: float = DEFAULT_RETRY_DELAY_S,
) -> None:
    """Flush, atomically replace with bounded retries, then verify exact bytes."""
    if attempts < 1:
        raise ValueError("attempts must be positive")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    )
    try:
        with open(temp, "xb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        _retry(lambda: os.replace(temp, path), attempts=attempts, delay_s=delay_s)
        actual = read_bytes(path, attempts=attempts, delay_s=delay_s)
        if actual != payload:
            raise OSError("atomic write readback mismatch")
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def read_bytes(
    path: Path, *, attempts: int = DEFAULT_RETRY_ATTEMPTS,
    delay_s: float = DEFAULT_RETRY_DELAY_S,
) -> bytes:
    return _retry(path.read_bytes, attempts=attempts, delay_s=delay_s)


def unlink_with_retry(
    path: Path, *, missing_ok: bool = False,
    validate_owner: Callable[[], bool] | None = None,
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    delay_s: float = DEFAULT_RETRY_DELAY_S,
) -> None:
    """Unlink with bounded retry, revalidating ownership before each retry."""
    last_error: OSError | None = None
    for index in range(attempts):
        if validate_owner is not None and not validate_owner():
            raise OSError("ownership changed during unlink retry")
        try:
            path.unlink(missing_ok=missing_ok)
            return
        except OSError as exc:
            last_error = exc
            if index + 1 >= attempts:
                break
            time.sleep(delay_s)
    assert last_error is not None
    raise last_error


def _retry(
    operation: Callable[[], bytes | None], *, attempts: int, delay_s: float,
):
    last_error: OSError | None = None
    for index in range(attempts):
        try:
            return operation()
        except OSError as exc:
            last_error = exc
            if index + 1 >= attempts:
                break
            time.sleep(delay_s)
    assert last_error is not None
    raise last_error

"""Deterministic tests for bounded Windows-style durable I/O retries."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from reticolo_mcp import durable_io


def test_atomic_write_retries_replace_and_verifies(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    real_replace = durable_io.os.replace
    calls = 0

    def flaky_replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("sharing violation")
        return real_replace(source, target)

    monkeypatch.setattr(durable_io.os, "replace", flaky_replace)
    monkeypatch.setattr(durable_io.time, "sleep", lambda _delay: None)
    durable_io.atomic_write_bytes(path, b"payload", attempts=2, delay_s=0)
    assert path.read_bytes() == b"payload"
    assert calls == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_fails_bounded_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    monkeypatch.setattr(
        durable_io.os, "replace", MagicMock(side_effect=PermissionError("busy")),
    )
    monkeypatch.setattr(durable_io.time, "sleep", lambda _delay: None)
    with pytest.raises(PermissionError):
        durable_io.atomic_write_bytes(path, b"payload", attempts=2, delay_s=0)
    assert not path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_unlink_revalidates_owner_before_retry(tmp_path, monkeypatch):
    path = tmp_path / "lease.json"
    path.write_text("owned", encoding="utf-8")
    real_unlink = durable_io.Path.unlink
    calls = 0
    ownership = [True, False]

    def flaky_unlink(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("busy")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(durable_io.Path, "unlink", flaky_unlink)
    monkeypatch.setattr(durable_io.time, "sleep", lambda _delay: None)
    with pytest.raises(OSError, match="ownership changed"):
        durable_io.unlink_with_retry(
            path, validate_owner=lambda: ownership.pop(0), attempts=2, delay_s=0,
        )
    assert path.exists()

"""Unit tests for solver lease — no MATLAB or COMSOL required."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from reticolo_mcp.lease import (
    _is_pid_alive,
    lease_acquire,
    lease_release,
    lease_status,
)


class TestIsPidAlive:
    def test_own_pid(self):
        assert _is_pid_alive(os.getpid()) is True

    def test_zero(self):
        assert _is_pid_alive(0) is False

    def test_negative(self):
        assert _is_pid_alive(-1) is False

    def test_huge_pid(self):
        assert _is_pid_alive(99999999) is False


class TestLeaseLifecycle:
    @pytest.fixture(autouse=True)
    def _isolated_lease(self, tmp_path, monkeypatch):
        lease_path = tmp_path / "reticolo_lease.json"
        monkeypatch.setattr("reticolo_mcp.lease.LEASE_PATH", lease_path)
        monkeypatch.setattr("reticolo_mcp.lease.RUNTIME_DIR", tmp_path)
        monkeypatch.setattr(
            "reticolo_mcp.lease._comsol_lease_path", lambda: None)
        self.lease_path = lease_path
        yield
        lease_path.unlink(missing_ok=True)

    def test_initial_status_no_lease(self):
        s = lease_status()
        assert s["reticolo_lease"]["active"] is False
        assert s["collision"] is False
        assert s["ready"] is True

    def test_acquire_and_release(self):
        r = lease_acquire("test")
        assert r["acquired"] is True
        assert self.lease_path.is_file()

        s = lease_status()
        assert s["reticolo_lease"]["active"] is True
        assert s["ready"] is True

        r = lease_release()
        assert r["released"] is True
        assert not self.lease_path.exists()

    def test_release_without_lease(self):
        r = lease_release()
        assert r["released"] is False

    def test_status_with_stale_lease(self):
        lease_acquire("test")
        data = '{"schema":"1","owner":"reticolo-mcp","pid":99999,"created_at":0,"label":"dead"}'
        self.lease_path.write_text(data)
        s = lease_status()
        assert s["reticolo_lease"]["active"] is False
        lease_release()

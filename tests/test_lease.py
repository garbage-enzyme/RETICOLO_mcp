"""Unit tests for solver lease — no MATLAB or COMSOL required."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from reticolo_mcp.lease import (
    _is_pid_alive,
    _process_creation_date,
    lease_acquire,
    lease_heartbeat,
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

    def test_release_rejects_wrong_token(self):
        acquired = lease_acquire("test")
        result = lease_release("wrong-token")
        assert result["released"] is False
        assert self.lease_path.exists()
        assert lease_release(acquired["token"])["released"] is True

    def test_status_with_stale_lease(self):
        lease_acquire("test")
        data = '{"schema":"1","owner":"reticolo-mcp","pid":99999,"created_at":0,"label":"dead"}'
        self.lease_path.write_text(data)
        s = lease_status()
        assert s["reticolo_lease"]["active"] is False
        lease_release()

    def test_heartbeat_updates(self):
        r = lease_acquire("test")
        assert r["acquired"] is True
        token = r["token"]

        import time
        time.sleep(0.1)
        ok = lease_heartbeat(token)
        assert ok is True
        assert self.lease_path.is_file()

        import json
        data = json.loads(self.lease_path.read_text())
        assert data["heartbeat"] > data["created_at"]

        lease_release()

    def test_heartbeat_wrong_token(self):
        lease_acquire("test")
        ok = lease_heartbeat("wrong-token")
        assert ok is False
        lease_release()

    def test_heartbeat_no_active_lease(self):
        ok = lease_heartbeat("any-token")
        assert ok is False


class TestProcessCreationDate:
    def test_own_pid(self):
        import os
        cdate = _process_creation_date(os.getpid())
        assert cdate is not None
        assert isinstance(cdate, float)
        assert cdate > 0

    def test_zero_pid(self):
        assert _process_creation_date(0) is None

    def test_negative_pid(self):
        assert _process_creation_date(-1) is None

    def test_dead_pid(self):
        assert _process_creation_date(99999999) is None

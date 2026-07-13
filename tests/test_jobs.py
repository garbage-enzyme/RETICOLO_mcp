"""Unit tests for durable job store — no MATLAB required."""

from __future__ import annotations

import json
import time

import pytest

from reticolo_mcp.jobs import (
    _compute_spec_hash,
    append_event,
    create_job_spec,
    read_events,
    read_spec,
    read_state,
    results_path,
    write_spec,
    write_state,
    worker_log_path,
)


class TestSpecHash:
    def test_deterministic(self):
        s1 = create_job_spec(
            wls_um=[5.0], D=[1.0], nn=[5, 5],
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
        )
        s2 = create_job_spec(
            wls_um=[5.0], D=[1.0], nn=[5, 5],
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
        )
        assert s1["config_hash"] == s2["config_hash"]

    def test_different_wl_gives_different_hash(self):
        s1 = create_job_spec(wls_um=[5.0], D=[1.0], nn=[5, 5],
                             textures=[1.0],
                             profil={"heights":[0,0],"indices":[1,1]})
        s2 = create_job_spec(wls_um=[5.1], D=[1.0], nn=[5, 5],
                             textures=[1.0],
                             profil={"heights":[0,0],"indices":[1,1]})
        assert s1["config_hash"] != s2["config_hash"]


class TestJobStore:
    @pytest.fixture(autouse=True)
    def _isolated(self, tmp_path, monkeypatch):
        monkeypatch.setattr("reticolo_mcp.jobs.RUNTIME_DIR", tmp_path)
        self.job_id = "test-job-001"

    def test_spec_write_read(self):
        spec = create_job_spec(wls_um=[5.0], D=[1.0], nn=[5, 5],
                               textures=[1.0],
                               profil={"heights":[0,0],"indices":[1,1]})
        write_spec(self.job_id, spec)
        read = read_spec(self.job_id)
        assert read is not None
        assert read["config_hash"] == spec["config_hash"]

    def test_spec_reject_mutation(self):
        spec1 = create_job_spec(wls_um=[5.0], D=[1.0], nn=[5, 5],
                                textures=[1.0],
                                profil={"heights":[0,0],"indices":[1,1]})
        spec2 = create_job_spec(wls_um=[5.1], D=[1.0], nn=[5, 5],
                                textures=[1.0],
                                profil={"heights":[0,0],"indices":[1,1]})
        write_spec(self.job_id, spec1)
        with pytest.raises(ValueError, match="spec changed"):
            write_spec(self.job_id, spec2)

    def test_state_write_read(self):
        write_state(self.job_id, {"status": "running", "worker_pid": 1234})
        s = read_state(self.job_id)
        assert s is not None
        assert s["status"] == "running"
        assert s["worker_pid"] == 1234

    def test_state_reject_invalid_status(self):
        with pytest.raises(ValueError, match="invalid status"):
            write_state(self.job_id, {"status": "nonsense"})

    def test_events_append_and_read(self):
        append_event(self.job_id, {"event": "start"})
        append_event(self.job_id, {"event": "point", "wl": 5.0})
        events = read_events(self.job_id)
        assert len(events) == 2
        assert events[0]["event"] == "start"

    def test_events_tail(self):
        for i in range(10):
            append_event(self.job_id, {"event": f"e{i}"})
        events = read_events(self.job_id, tail=3)
        assert len(events) == 3
        assert events[-1]["event"] == "e9"

    def test_paths(self):
        assert "test-job-001" in str(results_path(self.job_id))
        assert "worker.log" in str(worker_log_path(self.job_id))

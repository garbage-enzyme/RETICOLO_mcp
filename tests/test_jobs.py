"""Unit tests for durable job store — no MATLAB required."""

from __future__ import annotations

import json
import time
from pathlib import Path

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
    transition_state,
    verify_event_chain,
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

    def test_deterministic_across_timestamp_change(self, monkeypatch):
        monkeypatch.setattr("reticolo_mcp.jobs.time.time", lambda: 100.0)
        s1 = create_job_spec(
            wls_um=[5.0], D=[1.0], nn=[5, 5],
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
        )
        monkeypatch.setattr("reticolo_mcp.jobs.time.time", lambda: 200.0)
        s2 = create_job_spec(
            wls_um=[5.0], D=[1.0], nn=[5, 5],
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
        )
        assert s1["created_at"] != s2["created_at"]
        assert s1["physical_config_hash"] == s2["physical_config_hash"]
        assert s1["job_spec_hash"] == s2["job_spec_hash"]

    def test_label_does_not_change_physical_or_job_identity(self):
        common = dict(
            wls_um=[5.0], D=[1.0], nn=[5, 5],
            textures=[1.0], profil={"heights": [0, 0], "indices": [1, 1]},
        )
        s1 = create_job_spec(**common, config_label="first")
        s2 = create_job_spec(**common, config_label="second")
        assert s1["physical_config_hash"] == s2["physical_config_hash"]
        assert s1["job_spec_hash"] == s2["job_spec_hash"]

    def test_different_wl_gives_different_hash(self):
        s1 = create_job_spec(wls_um=[5.0], D=[1.0], nn=[5, 5],
                             textures=[1.0],
                             profil={"heights":[0,0],"indices":[1,1]})
        s2 = create_job_spec(wls_um=[5.1], D=[1.0], nn=[5, 5],
                             textures=[1.0],
                             profil={"heights":[0,0],"indices":[1,1]})
        assert s1["config_hash"] != s2["config_hash"]

    def test_point_textures_are_normalized_and_identity_bound(self):
        common = dict(
            wls_um=[5.0, 5.1], D=[1.0], nn=[5, 5], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
        )
        s1 = create_job_spec(
            **common, point_textures=[[[1.0, 0.0]], [[1.1, 0.1]]],
        )
        s2 = create_job_spec(
            **common, point_textures=[[[1.0, 0.0]], [[1.2, 0.1]]],
        )
        assert s1["schema"] == "2"
        assert s1["point_textures"] == [[[1.0, 0.0]], [[1.1, 0.1]]]
        assert s1["physical_config_hash"] != s2["physical_config_hash"]
        assert s1["job_spec_hash"] != s2["job_spec_hash"]

    def test_point_textures_reject_misalignment_and_duplicate_wavelengths(self):
        common = dict(
            D=[1.0], nn=[5, 5], textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
        )
        with pytest.raises(ValueError, match="one-to-one"):
            create_job_spec(
                **common, wls_um=[5.0, 5.1], point_textures=[[1.0]],
            )
        with pytest.raises(ValueError, match="unique wavelengths"):
            create_job_spec(
                **common, wls_um=[5.0, 5.0],
                point_textures=[[1.0], [1.1]],
            )


class TestJobStore:
    @pytest.fixture(autouse=True)
    def _isolated(self, tmp_path, monkeypatch):
        monkeypatch.setattr("reticolo_mcp.jobs.RUNTIME_DIR", tmp_path)
        self.job_id = "test-job-001"
        self.runtime_path = tmp_path

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

    def test_transition_requires_status_and_attempt(self):
        write_state(self.job_id, {
            "status": "running", "attempt_id": "attempt-1", "attempt": 1,
        })
        stale = transition_state(
            self.job_id, allowed_from={"running"}, attempt_id="attempt-old",
            updates={"status": "cancel_requested"},
        )
        assert stale["updated"] is False
        assert stale["reason"] == "stale_attempt"
        assert read_state(self.job_id)["status"] == "running"

        valid = transition_state(
            self.job_id, allowed_from={"running"}, attempt_id="attempt-1",
            updates={"status": "cancel_requested"},
        )
        assert valid["updated"] is True
        assert read_state(self.job_id)["status"] == "cancel_requested"

    def test_second_transition_cannot_overwrite_first(self):
        write_state(self.job_id, {
            "status": "interrupted", "attempt_id": "attempt-1", "attempt": 1,
        })
        first = transition_state(
            self.job_id, allowed_from={"interrupted"}, attempt_id="attempt-1",
            updates={"status": "submitted", "attempt_id": "attempt-2", "attempt": 2},
        )
        second = transition_state(
            self.job_id, allowed_from={"interrupted"}, attempt_id="attempt-1",
            updates={"status": "submitted", "attempt_id": "attempt-3", "attempt": 2},
        )
        assert first["updated"] is True
        assert second["updated"] is False
        assert read_state(self.job_id)["attempt_id"] == "attempt-2"

    def test_events_append_and_read(self):
        append_event(self.job_id, {"event": "start"})
        append_event(self.job_id, {"event": "point", "wl": 5.0})
        events = read_events(self.job_id)
        assert len(events) == 2
        assert events[0]["event"] == "start"
        assert [event["sequence"] for event in events] == [1, 2]
        assert verify_event_chain(self.job_id)["valid"] is True

    def test_events_tail(self):
        for i in range(10):
            append_event(self.job_id, {"event": f"e{i}"})
        events = read_events(self.job_id, tail=3)
        assert len(events) == 3
        assert events[-1]["event"] == "e9"

    def test_events_tail_is_bounded(self):
        from reticolo_mcp.jobs import MAX_EVENT_TAIL
        for i in range(MAX_EVENT_TAIL + 5):
            append_event(self.job_id, {"event": f"e{i}"})
        events = read_events(self.job_id, tail=MAX_EVENT_TAIL + 1000)
        assert len(events) == MAX_EVENT_TAIL
        assert events[-1]["event"] == f"e{MAX_EVENT_TAIL + 4}"

    def test_event_chain_detects_tamper(self):
        append_event(self.job_id, {"event": "start"})
        path = self.runtime_path / "jobs" / self.job_id / "events.jsonl"
        text = path.read_text(encoding="utf-8").replace('"start"', '"changed"')
        path.write_text(text, encoding="utf-8")
        result = verify_event_chain(self.job_id)
        assert result["valid"] is False
        assert result["reason"] == "hash"
        with pytest.raises(ValueError, match="tampered"):
            append_event(self.job_id, {"event": "must-not-append"})

    def test_event_size_cap(self, monkeypatch):
        monkeypatch.setattr("reticolo_mcp.jobs.MAX_EVENT_BYTES", 200)
        with pytest.raises(ValueError, match="MAX_EVENT_BYTES"):
            append_event(self.job_id, {"event": "x", "payload": "y" * 500})

    def test_read_missing_job_has_no_write_side_effect(self, tmp_path):
        assert read_state("missing-job") is None
        assert read_spec("missing-job") is None
        assert not (tmp_path / "jobs" / "missing-job").exists()

    @pytest.mark.parametrize(
        "job_id",
        ["../escape", "..\\escape", "D:\\escape", "/escape", "", "a/b"],
    )
    def test_invalid_job_id_rejected(self, job_id):
        with pytest.raises(ValueError, match="invalid job_id"):
            read_state(job_id)

    def test_paths(self):
        assert "test-job-001" in str(results_path(self.job_id))
        assert "worker.log" in str(worker_log_path(self.job_id))

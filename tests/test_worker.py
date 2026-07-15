"""Unit tests for worker helpers — no MATLAB required."""

from __future__ import annotations

from io import BytesIO

from reticolo_mcp.worker import (
    _BoundedLogWriter, _admit_point, _cancel_requested, _finalize_cleanup,
    _to_complex,
)


class TestToComplex:
    def test_constant_n(self):
        result = _to_complex([1.5])
        assert result == [1.5]

    def test_complex_pair(self):
        result = _to_complex([[4.0, 0.001]])
        assert result == [complex(4.0, 0.001)]

    def test_patterned_layer(self):
        result = _to_complex([
            [1.0, [0.0, 0.0, 0.3, 0.3, 4.0, 0.001, 1]]
        ])
        assert len(result) == 1
        assert isinstance(result[0], list)
        assert result[0][0] == 1.0
        assert result[0][1] == [0.0, 0.0, 0.3, 0.3, complex(4.0, 0.001), 1]

    def test_mixed_complex_in_pattern(self):
        result = _to_complex([
            [[1.0, 0.0], [0.0, 0.0, 0.3, 0.3, [4.0, 0.001], 1]]
        ])
        assert len(result) == 1
        assert isinstance(result[0], list)
        assert result[0][0] == complex(1.0, 0.0)
        assert len(result[0][1]) == 6
        assert result[0][1][0:4] == [0.0, 0.0, 0.3, 0.3]
        assert result[0][1][4] == complex(4.0, 0.001)
        assert result[0][1][5] == 1

    def test_empty_list(self):
        assert _to_complex([]) == []

    def test_nested_list_no_complex(self):
        result = _to_complex([[1.0, [0.0, 0.0, 0.3, 0.3, 4.0, 1]]])
        assert result == [[1.0, [0.0, 0.0, 0.3, 0.3, 4.0, 1]]]

    def test_multiple_layers(self):
        result = _to_complex([
            [1.0, 0.0],
            [3.5, 0.001],
            [1.0, 0.0],
        ])
        assert result == [
            complex(1.0, 0.0),
            complex(3.5, 0.001),
            complex(1.0, 0.0),
        ]

    def test_plain_numbers_passthrough(self):
        result = _to_complex([1.0, 1.5, 1.0])
        assert result == [1.0, 1.5, 1.0]


class TestCancelControl:
    def test_cancel_requested_state(self, monkeypatch):
        monkeypatch.setattr(
            "reticolo_mcp.worker.read_state",
            lambda _job_id: {"status": "cancel_requested"},
        )
        assert _cancel_requested("job-abc") is True

    def test_running_state(self, monkeypatch):
        monkeypatch.setattr(
            "reticolo_mcp.worker.read_state",
            lambda _job_id: {"status": "running"},
        )
        assert _cancel_requested("job-abc") is False

    def test_missing_state(self, monkeypatch):
        monkeypatch.setattr("reticolo_mcp.worker.read_state", lambda _job_id: None)
        assert _cancel_requested("job-abc") is False

    def test_stale_attempt_cancel_is_ignored(self, monkeypatch):
        monkeypatch.setattr(
            "reticolo_mcp.worker.read_state",
            lambda _job_id: {
                "status": "cancel_requested", "attempt_id": "new-attempt",
            },
        )
        assert _cancel_requested("job-abc", "old-attempt") is False

    def test_matching_attempt_cancel_is_observed(self, monkeypatch):
        monkeypatch.setattr(
            "reticolo_mcp.worker.read_state",
            lambda _job_id: {
                "status": "cancel_requested", "attempt_id": "attempt-1",
            },
        )
        assert _cancel_requested("job-abc", "attempt-1") is True


class TestBoundedLogWriter:
    def test_truncates_at_byte_budget(self):
        raw = BytesIO()
        writer = _BoundedLogWriter(raw, 5)
        assert writer.write("abcdef") == 6
        assert raw.getvalue() == b"abcde"
        assert writer.truncated is True
        writer.write("more")
        assert raw.getvalue() == b"abcde"

    def test_does_not_split_utf8_codepoint(self):
        raw = BytesIO()
        writer = _BoundedLogWriter(raw, 2)
        writer.write("中a")
        assert raw.getvalue().decode("utf-8") == ""


def test_point_admission_persists_evidence(monkeypatch):
    from reticolo_mcp.resources import ResourceSnapshot

    policy = {
        "min_available_memory_fraction": 0.1,
        "warning_available_memory_fraction": 0.2,
        "min_commit_remaining_fraction": 0.1,
        "warning_commit_remaining_fraction": 0.2,
        "min_runtime_free_fraction": 0.1,
        "warning_runtime_free_fraction": 0.2,
        "max_points": 10, "wall_budget_s": 3600,
        "min_next_point_time_s": 60,
    }
    monkeypatch.setattr(
        "reticolo_mcp.worker.sample_resources",
        lambda **_kwargs: ResourceSnapshot(
            available_memory_fraction=0.5, commit_remaining_fraction=0.5,
            runtime_free_fraction=0.5, remaining_wall_s=3500,
        ),
    )
    events = []
    monkeypatch.setattr("reticolo_mcp.worker.append_event", lambda _job, event: events.append(event))
    decision = _admit_point("job-abc", "attempt-1", {"resource_policy": policy}, 5.0, 0.0)
    assert decision["decision"] == "green"
    assert events[0]["event"] == "pre_point_resource_admission"


class TestFinalCleanup:
    def test_proven_cleanup_records_bound_exit(self, monkeypatch):
        engine = type("Engine", (), {"stop": lambda self: {"status": "stopped"}})()
        events = []
        monkeypatch.setattr(
            "reticolo_mcp.worker.append_event",
            lambda _job_id, event: events.append(event),
        )
        monkeypatch.setattr("reticolo_mcp.worker._log", lambda *_args: None)
        assert _finalize_cleanup("job-abc", "attempt-1", engine) is True
        assert events == [{
            "event": "worker_exited",
            "attempt_id": "attempt-1",
            "cleanup_proven": True,
        }]

    def test_uncertain_cleanup_overrides_completed_state(self, monkeypatch):
        engine = type("Engine", (), {"stop": lambda self: {
            "status": "cleanup_uncertain",
            "error_code": "matlab_quit_failed",
            "detail": "quit failed",
            "connected": True,
        }})()
        transitions = []
        events = []
        monkeypatch.setattr(
            "reticolo_mcp.worker.read_state",
            lambda _job_id: {"status": "completed", "attempt_id": "attempt-1"},
        )
        monkeypatch.setattr(
            "reticolo_mcp.worker.transition_state",
            lambda *args, **kwargs: transitions.append((args, kwargs))
            or {"updated": True},
        )
        monkeypatch.setattr(
            "reticolo_mcp.worker.append_event",
            lambda _job_id, event: events.append(event),
        )
        monkeypatch.setattr("reticolo_mcp.worker._log", lambda *_args: None)
        assert _finalize_cleanup("job-abc", "attempt-1", engine) is False
        assert transitions[0][1]["allowed_from"] == {"completed"}
        assert transitions[0][1]["updates"]["status"] == "cleanup_uncertain"
        assert events[0]["event"] == "worker_cleanup_uncertain"

    def test_stop_exception_is_bounded_cleanup_uncertainty(self, monkeypatch):
        def fail_stop():
            raise RuntimeError("x" * 1000)

        engine = type("Engine", (), {"stop": lambda self: fail_stop()})()
        updates = []
        monkeypatch.setattr(
            "reticolo_mcp.worker.read_state",
            lambda _job_id: {"status": "failed", "attempt_id": "attempt-1"},
        )
        monkeypatch.setattr(
            "reticolo_mcp.worker.transition_state",
            lambda *args, **kwargs: updates.append(kwargs["updates"])
            or {"updated": True},
        )
        monkeypatch.setattr("reticolo_mcp.worker.append_event", lambda *_args: None)
        monkeypatch.setattr("reticolo_mcp.worker._log", lambda *_args: None)
        assert _finalize_cleanup("job-abc", "attempt-1", engine) is False
        assert updates[0]["cleanup"]["error_code"] == "engine_stop_raised"
        assert len(updates[0]["cleanup"]["detail"]) == 500

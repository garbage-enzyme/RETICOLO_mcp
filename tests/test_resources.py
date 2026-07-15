"""Pure resource policy and admission tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reticolo_mcp.resources import ResourcePolicy, ResourceSnapshot, evaluate_admission


def _policy(**updates) -> ResourcePolicy:
    values = dict(
        min_available_memory_fraction=0.20,
        warning_available_memory_fraction=0.30,
        min_commit_remaining_fraction=0.15,
        warning_commit_remaining_fraction=0.25,
        min_runtime_free_fraction=0.10,
        warning_runtime_free_fraction=0.20,
        max_points=20,
        wall_budget_s=3600,
        min_next_point_time_s=120,
    )
    values.update(updates)
    return ResourcePolicy(**values)


def _snapshot(**updates) -> ResourceSnapshot:
    values = dict(
        available_memory_fraction=0.50,
        commit_remaining_fraction=0.50,
        runtime_free_fraction=0.50,
        remaining_wall_s=3600,
    )
    values.update(updates)
    return ResourceSnapshot(**values)


def test_green_warning_and_refuse_decisions():
    assert evaluate_admission(_policy(), _snapshot(), point_count=5)["decision"] == "green"
    warning = evaluate_admission(
        _policy(), _snapshot(available_memory_fraction=0.25), point_count=5,
    )
    assert warning["decision"] == "warning"
    refused = evaluate_admission(
        _policy(), _snapshot(commit_remaining_fraction=0.10), point_count=5,
    )
    assert refused["decision"] == "refuse"
    assert "commit_remaining" in refused["failed"]


def test_missing_metric_fails_closed():
    result = evaluate_admission(
        _policy(), _snapshot(runtime_free_fraction=None), point_count=5,
    )
    assert result["decision"] == "refuse"
    assert result["reason"] == "required_metric_unavailable"


def test_point_and_wall_limits_refuse():
    assert evaluate_admission(_policy(), _snapshot(), point_count=21)["reason"] == "point_limit"
    result = evaluate_admission(
        _policy(), _snapshot(remaining_wall_s=60), point_count=5,
    )
    assert result["decision"] == "refuse"
    assert "wall_budget" in result["failed"]


def test_policy_hash_is_deterministic():
    first = evaluate_admission(_policy(), _snapshot(), point_count=5)
    second = evaluate_admission(_policy(), _snapshot(), point_count=5)
    assert first["policy_hash"] == second["policy_hash"]
    assert len(first["policy_hash"]) == 64


def test_invalid_threshold_relationship_rejected():
    with pytest.raises(ValidationError, match="warning threshold"):
        _policy(warning_available_memory_fraction=0.10)


def test_unknown_policy_field_rejected():
    values = _policy().model_dump()
    values["host_default"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResourcePolicy.model_validate(values)

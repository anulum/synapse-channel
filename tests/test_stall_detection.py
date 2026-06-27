# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — predictive stall detection policy tests

from __future__ import annotations

from pathlib import Path
from typing import Any

from synapse_channel.core.stall import StallPolicy, detect_stalls

REPO_ROOT = Path(__file__).resolve().parents[1]


def _board(
    tasks: list[dict[str, Any]], progress: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"tasks": tasks, "progress": progress or [], "ready": []}


def test_fixed_idle_threshold_still_reoffers_stalled_task() -> None:
    board = _board([{"task_id": "T1", "status": "in_progress", "updated_at": 0.0}])

    out = detect_stalls(board, now=1000.0, policy=StallPolicy(idle_seconds=300.0))

    assert [item.task_id for item in out] == ["T1"]
    assert out[0].action == "reoffer"
    assert out[0].reason == "no progress in 300s"


def test_predictive_history_reoffers_before_fixed_idle_ceiling() -> None:
    board = _board(
        [
            {"task_id": "DONE1", "status": "done", "created_at": 0.0, "updated_at": 90.0},
            {"task_id": "DONE2", "status": "done", "created_at": 100.0, "updated_at": 190.0},
            {"task_id": "ACTIVE", "status": "in_progress", "updated_at": 200.0},
        ],
        progress=[
            {"task_id": "DONE1", "posted_at": 30.0},
            {"task_id": "DONE1", "posted_at": 60.0},
            {"task_id": "DONE2", "posted_at": 130.0},
            {"task_id": "DONE2", "posted_at": 160.0},
        ],
    )

    out = detect_stalls(
        board,
        now=350.0,
        policy=StallPolicy(
            idle_seconds=300.0,
            predictive=True,
            history_multiplier=3.0,
            min_history_samples=4,
            min_predictive_idle_seconds=60.0,
        ),
    )

    assert [item.task_id for item in out] == ["ACTIVE"]
    assert out[0].reason == "no progress in 90s (historical cadence)"


def test_predictive_history_formats_fractional_thresholds() -> None:
    board = _board(
        [
            {"task_id": "DONE", "status": "done", "created_at": 1.0, "updated_at": 52.0},
            {"task_id": "ACTIVE", "status": "in_progress", "updated_at": 100.0},
        ],
        progress=[{"task_id": "DONE", "posted_at": 26.5}],
    )

    out = detect_stalls(
        board,
        now=180.0,
        policy=StallPolicy(
            idle_seconds=300.0,
            predictive=True,
            history_multiplier=3.0,
            min_history_samples=2,
            min_predictive_idle_seconds=1.0,
        ),
    )

    assert [item.task_id for item in out] == ["ACTIVE"]
    assert out[0].reason == "no progress in 76.500s (historical cadence)"


def test_predictive_history_can_be_disabled() -> None:
    board = _board(
        [
            {"task_id": "DONE", "status": "done", "created_at": 0.0, "updated_at": 90.0},
            {"task_id": "ACTIVE", "status": "in_progress", "updated_at": 200.0},
        ],
        progress=[
            {"task_id": "DONE", "posted_at": 30.0},
            {"task_id": "DONE", "posted_at": 60.0},
        ],
    )

    out = detect_stalls(
        board,
        now=350.0,
        policy=StallPolicy(idle_seconds=300.0, predictive=False, min_history_samples=2),
    )

    assert out == []


def test_policy_uses_fixed_threshold_when_history_is_sparse() -> None:
    board = _board(
        [
            {"task_id": "DONE", "status": "done", "created_at": 0.0, "updated_at": 90.0},
            {"task_id": "ACTIVE", "status": "in_progress", "updated_at": 200.0},
        ],
        progress=[{"task_id": "DONE", "posted_at": 30.0}],
    )

    out = detect_stalls(
        board,
        now=350.0,
        policy=StallPolicy(idle_seconds=300.0, predictive=True, min_history_samples=4),
    )

    assert out == []


def test_policy_uses_fixed_threshold_when_history_is_slower_than_idle_ceiling() -> None:
    board = _board(
        [
            {"task_id": "DONE", "status": "done", "created_at": 1.0, "updated_at": 401.0},
            {"task_id": "ACTIVE", "status": "in_progress", "updated_at": 200.0},
        ],
        progress=[{"task_id": "DONE", "posted_at": 201.0}],
    )

    out = detect_stalls(
        board,
        now=510.0,
        policy=StallPolicy(idle_seconds=300.0, predictive=True, min_history_samples=2),
    )

    assert [item.task_id for item in out] == ["ACTIVE"]
    assert out[0].reason == "no progress in 300s"


def test_policy_clamps_operator_inputs() -> None:
    policy = StallPolicy(
        idle_seconds=-1.0,
        history_multiplier=0.0,
        min_history_samples=0,
        min_predictive_idle_seconds=-10.0,
        history_task_limit=0,
    )

    assert policy.idle_seconds == 1.0
    assert policy.history_multiplier == 1.0
    assert policy.min_history_samples == 1
    assert policy.min_predictive_idle_seconds == 1.0
    assert policy.history_task_limit == 1


def test_blocked_dependency_rule_remains_independent_of_prediction() -> None:
    board = _board(
        [
            {"task_id": "D", "status": "done", "updated_at": 10.0},
            {"task_id": "B", "status": "blocked", "updated_at": 20.0, "depends_on": ["D"]},
        ]
    )

    out = detect_stalls(
        board,
        now=25.0,
        policy=StallPolicy(idle_seconds=300.0, predictive=True),
    )

    assert [item.task_id for item in out] == ["B"]
    assert out[0].reason == "dependencies satisfied"


def test_malformed_activity_timestamps_do_not_crash_detection() -> None:
    board = _board(
        [{"task_id": "T1", "status": "in_progress", "updated_at": "not-a-time"}],
        progress=[{"task_id": "T1", "posted_at": object()}],
    )

    out = detect_stalls(board, now=1000.0, policy=StallPolicy(idle_seconds=300.0))

    assert [item.task_id for item in out] == ["T1"]


def test_malformed_progress_collections_do_not_crash_detection() -> None:
    board = {
        "tasks": [{"task_id": "T1", "status": "in_progress", "updated_at": 0.0}],
        "progress": "not-a-list",
    }
    noisy_board = {
        "tasks": [{"task_id": "T2", "status": "in_progress", "updated_at": 0.0}],
        "progress": [object()],
    }

    first = detect_stalls(board, now=1000.0, policy=StallPolicy(idle_seconds=300.0))
    second = detect_stalls(noisy_board, now=1000.0, policy=StallPolicy(idle_seconds=300.0))

    assert [item.task_id for item in first] == ["T1"]
    assert [item.task_id for item in second] == ["T2"]


def test_public_docs_describe_predictive_stall_boundaries() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "coordination-model.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "glossary.md").read_text(encoding="utf-8"),
        ]
    )

    assert "--no-predictive-stall" in combined
    assert "historical-cadence" in combined
    assert "not proof that a worker failed" in combined

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — recent GitHub Actions history classifier tests
"""Exercise deterministic history classification and its fail-visible CLI."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "audit_github_actions_history.py"
SPEC = importlib.util.spec_from_file_location("audit_github_actions_history", TOOL)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

audit_to_json = MODULE.audit_to_json
classify_workflow_runs = MODULE.classify_workflow_runs
has_unresolved_runs = MODULE.has_unresolved_runs
main = MODULE.main
workflow_run_from_mapping = MODULE.workflow_run_from_mapping
workflow_runs_from_json = MODULE.workflow_runs_from_json


class _WorkflowRunView(Protocol):
    database_id: int


class _ClassifiedRunView(Protocol):
    bucket: str
    run: _WorkflowRunView


def _run(
    database_id: int,
    conclusion: str | None,
    created_at: str,
    *,
    workflow: str = "ci",
    branch: str = "main",
    status: str = "completed",
) -> dict[str, object]:
    return {
        "databaseId": database_id,
        "conclusion": conclusion,
        "status": status,
        "workflowName": workflow,
        "headSha": f"sha-{database_id}",
        "headBranch": branch,
        "createdAt": created_at,
        "event": "push",
    }


def _classify(
    rows: list[dict[str, object]],
    *,
    excluded_workflows: tuple[str, ...] = (),
) -> tuple[_ClassifiedRunView, ...]:
    runs = workflow_runs_from_json(json.dumps(rows))
    return cast(
        tuple[_ClassifiedRunView, ...],
        classify_workflow_runs(runs, excluded_workflows=excluded_workflows),
    )


def test_failure_is_resolved_only_by_later_same_workflow_branch_success() -> None:
    classified = _classify(
        [
            _run(1, "failure", "2026-07-11T00:00:00Z"),
            _run(2, "success", "2026-07-11T01:00:00Z", workflow="docs"),
            _run(3, "success", "2026-07-11T02:00:00Z", branch="feature"),
            _run(4, "success", "2026-07-11T03:00:00Z"),
        ]
    )

    assert [item.bucket for item in classified] == [
        "resolved_failure",
        "clean_success",
        "clean_success",
        "clean_success",
    ]
    assert not has_unresolved_runs(classified)


def test_failure_without_later_matching_success_remains_unresolved() -> None:
    classified = _classify(
        [
            _run(10, "failure", "2026-07-11T00:00:00Z"),
            _run(11, "success", "2026-07-11T01:00:00Z", workflow="docs"),
        ]
    )

    assert classified[0].bucket == "unresolved_failure"
    assert has_unresolved_runs(classified)


def test_cancelled_run_requires_later_matching_success() -> None:
    classified = _classify(
        [
            _run(20, "cancelled", "2026-07-11T00:00:00Z"),
            _run(21, "cancelled", "2026-07-11T00:30:00Z", branch="feature"),
            _run(22, "success", "2026-07-11T01:00:00Z"),
        ]
    )

    assert classified[0].bucket == "superseded_cancelled"
    assert classified[1].bucket == "unresolved_cancelled"
    assert has_unresolved_runs(classified)


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "stale", "action_required"])
def test_bad_completed_conclusions_fail_without_later_success(conclusion: str) -> None:
    classified = _classify([_run(30, conclusion, "2026-07-11T00:00:00Z")])

    assert classified[0].bucket == "unresolved_failure"
    assert has_unresolved_runs(classified)


def test_later_success_resolves_timed_out_or_stale_history() -> None:
    classified = _classify(
        [
            _run(40, "timed_out", "2026-07-11T00:00:00Z"),
            _run(41, "stale", "2026-07-11T00:30:00Z"),
            _run(42, "success", "2026-07-11T01:00:00Z"),
        ]
    )

    assert [item.bucket for item in classified] == [
        "resolved_failure",
        "resolved_failure",
        "clean_success",
    ]


def test_live_and_non_failing_completed_conclusions_do_not_fail_audit() -> None:
    classified = _classify(
        [
            _run(50, None, "2026-07-11T00:00:00Z", status="in_progress"),
            _run(51, "neutral", "2026-07-11T00:30:00Z"),
            _run(52, "skipped", "2026-07-11T01:00:00Z"),
        ]
    )

    assert [item.bucket for item in classified] == [
        "in_progress",
        "other_completed",
        "other_completed",
    ]
    assert not has_unresolved_runs(classified)


def test_excluded_audit_workflow_cannot_latch_its_own_failure() -> None:
    classified = _classify(
        [
            _run(
                60,
                "failure",
                "2026-07-11T00:00:00Z",
                workflow="actions-history-audit",
            ),
            _run(61, "success", "2026-07-11T01:00:00Z"),
        ],
        excluded_workflows=("actions-history-audit",),
    )

    assert len(classified) == 1
    assert classified[0].run.database_id == 61
    assert not has_unresolved_runs(classified)


def test_equal_timestamps_use_database_id_as_stable_tie_breaker() -> None:
    classified = _classify(
        [
            _run(72, "success", "2026-07-11T00:00:00Z"),
            _run(71, "failure", "2026-07-11T00:00:00Z"),
        ]
    )

    assert [item.run.database_id for item in classified] == [71, 72]
    assert classified[0].bucket == "resolved_failure"


def test_json_output_is_deterministic_and_contains_summary() -> None:
    classified = _classify(
        [
            _run(81, "success", "2026-07-11T01:00:00Z"),
            _run(80, "failure", "2026-07-11T00:00:00Z"),
        ]
    )

    first = audit_to_json(classified, excluded_workflows=("z", "a", "z"))
    second = audit_to_json(classified, excluded_workflows=("a", "z"))

    assert first == second
    decoded = json.loads(first)
    assert decoded["schema"] == "synapse-actions-history-audit.v1"
    assert decoded["excludedWorkflows"] == ["a", "z"]
    assert decoded["summary"] == {"clean_success": 1, "resolved_failure": 1}
    assert [row["databaseId"] for row in decoded["runs"]] == [80, 81]


def test_naive_timestamp_is_normalised_to_utc() -> None:
    run = workflow_run_from_mapping(_run(90, "success", "2026-07-11T00:00:00"))
    encoded = audit_to_json(classify_workflow_runs((run,)))

    assert json.loads(encoded)["runs"][0]["createdAt"] == "2026-07-11T00:00:00Z"


@pytest.mark.parametrize(
    "payload, message",
    [
        (json.dumps({"databaseId": 1}), "must be an array"),
        (json.dumps([1]), "entries must be objects"),
        (json.dumps([_run(True, "success", "2026-07-11T00:00:00Z")]), "must be an integer"),
    ],
)
def test_parser_rejects_malformed_history(payload: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        workflow_runs_from_json(payload)


def test_cli_prints_evidence_before_returning_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = tmp_path / "history.json"
    history.write_text(
        json.dumps([_run(100, "failure", "2026-07-11T00:00:00Z")]),
        encoding="utf-8",
    )

    assert main(["--input", str(history)]) == 1
    decoded = json.loads(capsys.readouterr().out)
    assert decoded["summary"] == {"unresolved_failure": 1}


def test_cli_reads_stdin_and_excludes_itself(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                [_run(110, "failure", "2026-07-11T00:00:00Z", workflow="actions-history-audit")]
            )
        ),
    )

    assert main(["--input", "-", "--exclude-workflow", "actions-history-audit"]) == 0
    decoded = json.loads(capsys.readouterr().out)
    assert decoded["runs"] == []
    assert decoded["excludedWorkflows"] == ["actions-history-audit"]

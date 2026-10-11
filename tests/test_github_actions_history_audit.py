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


_WORKFLOW_IDS = {
    "ci": 299538792,
    "docs": 299538793,
    "actions-history-audit": 299538794,
    "Dependabot Updates": 299538861,
    "Dependency Graph": 300449968,
}
"""One number per workflow name used by the constructed rows of this file."""


def _run(
    database_id: int,
    conclusion: str | None,
    created_at: str,
    *,
    workflow: str = "ci",
    branch: str = "main",
    status: str = "completed",
    event: str = "push",
    title: str | None = None,
    workflow_id: int | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "databaseId": database_id,
        "conclusion": conclusion,
        "status": status,
        "workflowName": workflow,
        "workflowDatabaseId": _WORKFLOW_IDS[workflow] if workflow_id is None else workflow_id,
        "headSha": f"sha-{database_id}",
        "headBranch": branch,
        "createdAt": created_at,
        "event": event,
    }
    if title is not None:
        row["displayTitle"] = title
    return row


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
    assert decoded["schema"] == "synapse-actions-history-audit.v2"
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


# Records of the public repository's own history, as the run-list query returned
# them. The scheduled run 38047574697 was later removed from the host; its
# record is kept here as it was archived.
SCHEDULED_FAILURE = {
    "databaseId": 38047574697,
    "conclusion": "failure",
    "status": "completed",
    "workflowName": "vendor-watch",
    "workflowDatabaseId": 365136139,
    "headSha": "bbc9c427ae42ebd0c6abcb4ef3c35f33c1b530de",
    "headBranch": "main",
    "createdAt": "2026-10-10T11:12:13Z",
    "event": "schedule",
    "displayTitle": "vendor-watch",
}
MANUAL_SUCCESS = {
    "databaseId": 38148576401,
    "conclusion": "success",
    "status": "completed",
    "workflowName": "vendor-watch",
    "workflowDatabaseId": 365136139,
    "headSha": "d1e6d0629156a2b0830ee0c1418ba27e43ffa567",
    "headBranch": "main",
    "createdAt": "2026-10-11T14:46:57Z",
    "event": "workflow_dispatch",
    "displayTitle": "vendor-watch",
}
UPDATE_FAILURE_ONE_DIRECTORY = {
    "databaseId": 36967686236,
    "conclusion": "failure",
    "status": "completed",
    "workflowName": "Dependabot Updates",
    "workflowDatabaseId": 299538861,
    "headSha": "c5c2638bd31e2f5fcc35add5f2dcffddd47cc602",
    "headBranch": "main",
    "createdAt": "2026-10-02T05:09:33Z",
    "event": "dynamic",
    "displayTitle": "npm_and_yarn in /integrations/pi for brace-expansion - Update #1604460772",
}
UPDATE_SUCCESS_OTHER_DIRECTORY = {
    "databaseId": 37803817359,
    "conclusion": "success",
    "status": "completed",
    "workflowName": "Dependabot Updates",
    "workflowDatabaseId": 299538861,
    "headSha": "70186b75455837eab5ed77cf9673f6104e226132",
    "headBranch": "main",
    "createdAt": "2026-10-08T15:48:49Z",
    "event": "dynamic",
    "displayTitle": "npm_and_yarn in /clients/vscode for source-map-js - Update #1617630999",
}


def _buckets(rows: list[dict[str, object]]) -> dict[int, str]:
    return {item.run.database_id: item.bucket for item in _classify(rows)}


def test_manual_success_does_not_repair_a_scheduled_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows: list[dict[str, object]] = [dict(SCHEDULED_FAILURE), dict(MANUAL_SUCCESS)]
    assert _buckets(rows) == {38047574697: "unresolved_failure", 38148576401: "clean_success"}

    history = tmp_path / "history.json"
    history.write_text(json.dumps(rows), encoding="utf-8")
    assert main(["--input", str(history)]) == 1
    assert json.loads(capsys.readouterr().out)["summary"] == {
        "clean_success": 1,
        "unresolved_failure": 1,
    }


def test_later_scheduled_success_repairs_the_scheduled_failure() -> None:
    later = {**SCHEDULED_FAILURE, "databaseId": 38200000001, "conclusion": "success"}
    later["createdAt"] = "2026-10-12T11:12:13Z"
    rows: list[dict[str, object]] = [dict(SCHEDULED_FAILURE), dict(MANUAL_SUCCESS), later]

    assert _buckets(rows)[38047574697] == "resolved_failure"
    assert not has_unresolved_runs(_classify(rows))


def test_scheduled_success_does_not_repair_a_manual_failure() -> None:
    rows = [
        _run(1, "failure", "2026-07-11T00:00:00Z", event="workflow_dispatch"),
        _run(2, "success", "2026-07-11T01:00:00Z", event="schedule"),
    ]
    assert _buckets(rows)[1] == "unresolved_failure"


def test_update_success_in_another_directory_does_not_repair(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows: list[dict[str, object]] = [
        dict(UPDATE_FAILURE_ONE_DIRECTORY),
        dict(UPDATE_SUCCESS_OTHER_DIRECTORY),
    ]
    assert _buckets(rows) == {36967686236: "unresolved_failure", 37803817359: "clean_success"}

    history = tmp_path / "history.json"
    history.write_text(json.dumps(rows), encoding="utf-8")
    assert main(["--input", str(history)]) == 1
    decoded = json.loads(capsys.readouterr().out)
    assert [row["surface"] for row in decoded["runs"]] == [
        "npm_and_yarn in /integrations/pi for brace-expansion",
        "npm_and_yarn in /clients/vscode for source-map-js",
    ]
    assert decoded["runs"][0]["displayTitle"] == UPDATE_FAILURE_ONE_DIRECTORY["displayTitle"]


def test_later_update_of_the_same_surface_repairs_with_another_counter() -> None:
    title = "npm_and_yarn in /integrations/pi for brace-expansion - Update #1699999999"
    later = {**UPDATE_FAILURE_ONE_DIRECTORY, "databaseId": 38200000002, "conclusion": "success"}
    later.update(createdAt="2026-10-12T05:09:33Z", displayTitle=title)
    rows: list[dict[str, object]] = [dict(UPDATE_FAILURE_ONE_DIRECTORY), later]

    assert _buckets(rows)[36967686236] == "resolved_failure"


def test_same_directory_other_dependency_does_not_repair() -> None:
    title = "npm_and_yarn in /integrations/pi for minimatch - Update #1699999998"
    later = {**UPDATE_FAILURE_ONE_DIRECTORY, "databaseId": 38200000003, "conclusion": "success"}
    later.update(createdAt="2026-10-12T05:09:33Z", displayTitle=title)

    assert _buckets([dict(UPDATE_FAILURE_ONE_DIRECTORY), later])[36967686236] == (
        "unresolved_failure"
    )


@pytest.mark.parametrize(
    "title",
    [
        None,
        "",
        "Dependabot Updates",
        "#1604460772",
        "npm_and_yarn in /integrations/pi",
        "Dependabot Updates #1",
        "opaque #2",
        "Dependabot Updates - Update #1604460772",
        "npm_and_yarn in integrations/pi - Update #1604460772",
        "npm_and_yarn in /integrations/pi for  - Update #1604460772",
        "Graph Update: pip #1617567829",
        "Unconfigured Graph Update: pip in /. #1617567829",
        "npm_and_yarn in /integrations/pi - Update #",
    ],
)
def test_dynamic_failure_without_a_counted_title_fails_closed(title: str | None) -> None:
    rows = [
        _run(1, "failure", "2026-07-11T00:00:00Z", event="dynamic", title=title),
        _run(2, "success", "2026-07-11T01:00:00Z", event="dynamic", title=title),
    ]
    classified = _classify(rows)

    assert [item.bucket for item in classified] == ["unresolved_ambiguous", "clean_success"]
    assert has_unresolved_runs(classified)


def test_run_list_collected_without_titles_resolves_no_dynamic_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows: list[dict[str, object]] = []
    for record in (UPDATE_FAILURE_ONE_DIRECTORY, UPDATE_SUCCESS_OTHER_DIRECTORY):
        rows.append({key: value for key, value in record.items() if key != "displayTitle"})
    history = tmp_path / "history.json"
    history.write_text(json.dumps(rows), encoding="utf-8")

    assert main(["--input", str(history)]) == 1
    decoded = json.loads(capsys.readouterr().out)
    assert decoded["summary"] == {"clean_success": 1, "unresolved_ambiguous": 1}
    assert decoded["runs"][0]["surface"] is None


def test_cancelled_dynamic_run_of_unknown_surface_is_ambiguous_not_superseded() -> None:
    rows = [
        _run(1, "cancelled", "2026-07-11T00:00:00Z", event="dynamic"),
        _run(2, "success", "2026-07-11T01:00:00Z", event="dynamic"),
    ]
    assert _buckets(rows)[1] == "unresolved_ambiguous"


def test_graph_update_surfaces_are_told_apart_by_manifest() -> None:
    rows = [
        _run(
            1,
            "failure",
            "2026-07-11T00:00:00Z",
            workflow="Dependency Graph",
            event="dynamic",
            title="Configured Graph Update: pip in /. #1617567829",
        ),
        _run(
            2,
            "success",
            "2026-07-11T01:00:00Z",
            workflow="Dependency Graph",
            event="dynamic",
            title="Configured Graph Update: pip in /integrations/github-app #1618190634",
        ),
        _run(
            3,
            "success",
            "2026-07-11T02:00:00Z",
            workflow="Dependency Graph",
            event="dynamic",
            title="Configured Graph Update: pip in /. #1699999997",
        ),
    ]
    assert _buckets(rows[:2])[1] == "unresolved_failure"
    assert _buckets(rows)[1] == "resolved_failure"


def test_title_of_a_non_dynamic_run_does_not_split_its_history() -> None:
    rows = [
        _run(1, "failure", "2026-07-11T00:00:00Z", title="fix: one thing #12"),
        _run(2, "success", "2026-07-11T01:00:00Z", title="feat: another thing #13"),
    ]
    assert _buckets(rows)[1] == "resolved_failure"
    assert json.loads(audit_to_json(_classify(rows)))["runs"][0]["surface"] == ""


def test_display_title_of_the_wrong_kind_is_refused() -> None:
    row = _run(1, "success", "2026-07-11T00:00:00Z")
    row["displayTitle"] = 7
    with pytest.raises(ValueError, match="displayTitle"):
        workflow_run_from_mapping(row)


@pytest.mark.parametrize("field", ["status", "workflowName", "headSha", "headBranch", "event"])
@pytest.mark.parametrize("value", [None, "", 3])
def test_missing_or_empty_required_field_is_refused(field: str, value: object) -> None:
    row = _run(1, "success", "2026-07-11T00:00:00Z")
    row[field] = value
    with pytest.raises(ValueError, match=field):
        workflow_run_from_mapping(row)


@pytest.mark.parametrize(
    ("title", "surface"),
    [
        ("pip in /. - Update #1611599235", "pip in /."),
        (
            "github_actions in / for a/b, c/d - Update #1602928208",
            "github_actions in / for a/b, c/d",
        ),
        (
            "Graph Update: go_modules in /clients/go/synapse #1605017505",
            "Graph Update: go_modules in /clients/go/synapse",
        ),
        ("Configured Graph Update: pip in /. #1617567829", "Configured Graph Update: pip in /."),
    ],
)
def test_recognised_dynamic_titles_give_their_surface(title: str, surface: str) -> None:
    rows = [_run(1, "success", "2026-07-11T00:00:00Z", event="dynamic", title=title)]
    assert json.loads(audit_to_json(_classify(rows)))["runs"][0]["surface"] == surface


def test_same_name_under_another_workflow_number_does_not_repair(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [
        _run(1, "failure", "2026-07-11T00:00:00Z", workflow_id=501),
        _run(2, "success", "2026-07-11T01:00:00Z", workflow_id=502),
    ]
    history = tmp_path / "history.json"
    history.write_text(json.dumps(rows), encoding="utf-8")

    assert main(["--input", str(history)]) == 1
    decoded = json.loads(capsys.readouterr().out)
    assert decoded["summary"] == {"clean_success": 1, "unresolved_failure": 1}
    assert [row["workflowDatabaseId"] for row in decoded["runs"]] == [501, 502]
    assert {row["workflowName"] for row in decoded["runs"]} == {"ci"}


def test_renamed_workflow_with_one_number_still_repairs() -> None:
    rows = [
        _run(1, "failure", "2026-07-11T00:00:00Z", workflow="ci", workflow_id=501),
        _run(2, "success", "2026-07-11T01:00:00Z", workflow="docs", workflow_id=501),
    ]
    assert _buckets(rows)[1] == "resolved_failure"


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_run_list_without_workflow_numbers_resolves_nothing(
    conclusion: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [
        _run(1, conclusion, "2026-07-11T00:00:00Z"),
        _run(2, "success", "2026-07-11T01:00:00Z"),
    ]
    for row in rows:
        del row["workflowDatabaseId"]
    history = tmp_path / "history.json"
    history.write_text(json.dumps(rows), encoding="utf-8")

    assert main(["--input", str(history)]) == 1
    decoded = json.loads(capsys.readouterr().out)
    assert decoded["summary"] == {"clean_success": 1, "unresolved_ambiguous": 1}
    assert "no workflow number" in decoded["runs"][0]["reason"]
    assert decoded["runs"][0]["workflowDatabaseId"] is None


def test_success_without_a_workflow_number_does_not_repair_an_identified_failure() -> None:
    rows = [
        _run(1, "failure", "2026-07-11T00:00:00Z"),
        _run(2, "success", "2026-07-11T01:00:00Z"),
    ]
    rows[1]["workflowDatabaseId"] = None
    assert _buckets(rows) == {1: "unresolved_failure", 2: "clean_success"}


@pytest.mark.parametrize("value", [True, "299538792", 0, -4, 1.5])
def test_workflow_number_of_the_wrong_kind_is_refused(value: object) -> None:
    row = _run(1, "success", "2026-07-11T00:00:00Z")
    row["workflowDatabaseId"] = value
    with pytest.raises(ValueError, match="workflowDatabaseId"):
        workflow_run_from_mapping(row)


def test_ambiguous_reason_names_the_title_when_the_number_is_present() -> None:
    rows = [_run(1, "failure", "2026-07-11T00:00:00Z", event="dynamic", title="opaque #2")]
    assert (
        "dynamic title of no recognised form"
        in json.loads(audit_to_json(_classify(rows)))["runs"][0]["reason"]
    )

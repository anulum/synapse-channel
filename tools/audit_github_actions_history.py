# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — deterministic recent GitHub Actions history classifier
"""Classify a bounded GitHub Actions run-list without mutating remote state."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUDIT_SCHEMA = "synapse-actions-history-audit.v1"
NON_FAILURE_CONCLUSIONS = frozenset({"neutral", "skipped"})
UNRESOLVED_BUCKETS = frozenset({"unresolved_cancelled", "unresolved_failure"})


@dataclass(frozen=True)
class WorkflowRun:
    """Fields required to classify one GitHub Actions run."""

    database_id: int
    conclusion: str
    status: str
    workflow_name: str
    head_sha: str
    head_branch: str
    created_at: datetime
    event: str

    @property
    def workflow_key(self) -> tuple[str, str]:
        """Return the workflow and branch that later evidence must match."""
        return (self.workflow_name, self.head_branch)

    @property
    def chronology_key(self) -> tuple[datetime, int]:
        """Return the deterministic ordering key for equal timestamps."""
        return (self.created_at, self.database_id)


@dataclass(frozen=True)
class ClassifiedRun:
    """A workflow run paired with its evidence classification."""

    run: WorkflowRun
    bucket: str
    reason: str


def _required_text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Workflow run field {key!r} must be a non-empty string.")
    return value


def _optional_text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"Workflow run field {key!r} must be a string or null.")
    return value


def _parse_timestamp(value: str) -> datetime:
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def workflow_run_from_mapping(raw: Mapping[str, Any]) -> WorkflowRun:
    """Parse one record emitted by ``gh run list --json``."""
    database_id = raw.get("databaseId")
    if isinstance(database_id, bool) or not isinstance(database_id, int):
        raise ValueError("Workflow run field 'databaseId' must be an integer.")
    return WorkflowRun(
        database_id=database_id,
        conclusion=_optional_text(raw, "conclusion"),
        status=_required_text(raw, "status"),
        workflow_name=_required_text(raw, "workflowName"),
        head_sha=_required_text(raw, "headSha"),
        head_branch=_required_text(raw, "headBranch"),
        created_at=_parse_timestamp(_required_text(raw, "createdAt")),
        event=_required_text(raw, "event"),
    )


def workflow_runs_from_json(text: str) -> tuple[WorkflowRun, ...]:
    """Parse a JSON array produced by the bounded workflow-history query."""
    loaded = json.loads(text)
    if not isinstance(loaded, list):
        raise ValueError("Workflow history JSON must be an array.")
    runs: list[WorkflowRun] = []
    for item in loaded:
        if not isinstance(item, Mapping):
            raise ValueError("Workflow history entries must be objects.")
        runs.append(workflow_run_from_mapping(item))
    return tuple(runs)


def _later_success_exists(run: WorkflowRun, runs: Sequence[WorkflowRun]) -> bool:
    return any(
        candidate.workflow_key == run.workflow_key
        and candidate.chronology_key > run.chronology_key
        and candidate.status == "completed"
        and candidate.conclusion == "success"
        for candidate in runs
    )


def classify_workflow_runs(
    runs: Sequence[WorkflowRun],
    *,
    excluded_workflows: Sequence[str] = (),
) -> tuple[ClassifiedRun, ...]:
    """Classify runs, resolving bad history only with later matching success."""
    excluded = frozenset(excluded_workflows)
    ordered = tuple(
        sorted(
            (run for run in runs if run.workflow_name not in excluded),
            key=lambda run: run.chronology_key,
        )
    )
    classified: list[ClassifiedRun] = []
    for run in ordered:
        later_success = _later_success_exists(run, ordered)
        if run.status != "completed":
            bucket = "in_progress"
            reason = "Run is not completed; it remains live evidence."
        elif run.conclusion == "success":
            bucket = "clean_success"
            reason = "Run completed successfully."
        elif run.conclusion in NON_FAILURE_CONCLUSIONS:
            bucket = "other_completed"
            reason = f"Run completed with non-failing conclusion {run.conclusion!r}."
        elif run.conclusion == "cancelled" and later_success:
            bucket = "superseded_cancelled"
            reason = "A later success exists for the same workflow and branch."
        elif later_success:
            bucket = "resolved_failure"
            reason = "A later success exists for the same workflow and branch."
        elif run.conclusion == "cancelled":
            bucket = "unresolved_cancelled"
            reason = "Cancellation has no later matching success."
        else:
            bucket = "unresolved_failure"
            reason = f"Conclusion {run.conclusion!r} has no later matching success."
        classified.append(ClassifiedRun(run=run, bucket=bucket, reason=reason))
    return tuple(classified)


def audit_to_json(
    classified: Sequence[ClassifiedRun],
    *,
    excluded_workflows: Sequence[str] = (),
) -> str:
    """Serialize deterministic summary and per-run evidence as JSON."""
    counts: dict[str, int] = {}
    for item in classified:
        counts[item.bucket] = counts.get(item.bucket, 0) + 1
    rows = [
        {
            "bucket": item.bucket,
            "conclusion": item.run.conclusion,
            "createdAt": item.run.created_at.isoformat().replace("+00:00", "Z"),
            "databaseId": item.run.database_id,
            "event": item.run.event,
            "headBranch": item.run.head_branch,
            "headSha": item.run.head_sha,
            "reason": item.reason,
            "status": item.run.status,
            "workflowName": item.run.workflow_name,
        }
        for item in classified
    ]
    payload = {
        "excludedWorkflows": sorted(set(excluded_workflows)),
        "runs": rows,
        "schema": AUDIT_SCHEMA,
        "summary": dict(sorted(counts.items())),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def has_unresolved_runs(classified: Sequence[ClassifiedRun]) -> bool:
    """Return whether the bounded history contains an unresolved bad run."""
    return any(item.bucket in UNRESOLVED_BUCKETS for item in classified)


def _read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Classify one run-list and return nonzero for unresolved history."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Run-list JSON path, or '-' for stdin.")
    parser.add_argument(
        "--exclude-workflow",
        action="append",
        default=[],
        help="Workflow name omitted from classification; repeatable.",
    )
    args = parser.parse_args(argv)
    runs = workflow_runs_from_json(_read_input(args.input))
    classified = classify_workflow_runs(runs, excluded_workflows=args.exclude_workflow)
    print(audit_to_json(classified, excluded_workflows=args.exclude_workflow))
    return 1 if has_unresolved_runs(classified) else 0


if __name__ == "__main__":
    raise SystemExit(main())

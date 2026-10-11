# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — deterministic recent GitHub Actions history classifier
"""Classify a bounded GitHub Actions run-list without mutating remote state.

A failed or cancelled run counts as repaired only when a later run of **the
same thing** succeeded. The same thing is the same workflow, on the same
branch, started by the same event and, for a dynamically generated run, for
the same surface.

Input
-----
A JSON array as ``gh run list --json`` prints it, with these fields per run:
``databaseId``, ``conclusion``, ``status``, ``workflowName``,
``workflowDatabaseId``, ``headSha``, ``headBranch``, ``createdAt``, ``event``
and ``displayTitle``. The last two identity fields may be absent, as in a list
collected by an older query; what follows from their absence is said below.

Identity
--------
* **Workflow.** A workflow is identified by ``workflowDatabaseId``, the number
  the host gives the workflow file. The name is for people: two workflows can
  carry one name, and a name can be changed. The name is used only to leave a
  workflow out (``--exclude-workflow``).
* **Event.** A manual run that passes shows that the workflow works on that
  source. It does not show that the scheduled trigger works, so it does not
  repair a scheduled failure, and the reverse holds too.
* **Surface.** GitHub runs several unrelated jobs under one generic workflow
  with the event ``dynamic``. The run's display title says which one it was.
  Two title forms are recognised, exactly:

  - ``<ecosystem> in <directory>[ for <dependencies>] - Update #<counter>``
  - ``[Configured ]Graph Update: <ecosystem> in <directory> #<counter>``

  The surface is the title without the counter. A success for one directory,
  or for another dependency set in the same directory, is not a repair.

Ambiguity fails closed
----------------------
A run has no usable identity when its workflow number is absent, or when it is
a ``dynamic`` run whose title is absent or has neither recognised form (a
generic name with a counter is such a title). A bad run without identity is
reported as ``unresolved_ambiguous`` and fails the audit; a good run without
identity repairs nothing. A run list collected without ``workflowDatabaseId``
therefore resolves no failure at all, and one collected without
``displayTitle`` resolves no dynamic failure.

Output
------
Schema ``synapse-actions-history-audit.v2``: the excluded workflow names, a
count per bucket, and one row per run with its bucket, the reason, and the
``workflowDatabaseId``, ``displayTitle`` and ``surface`` the decision used.
The exit status is 1 when any run is ``unresolved_failure``,
``unresolved_cancelled`` or ``unresolved_ambiguous``.

The classification reads the list it is given and nothing else.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUDIT_SCHEMA = "synapse-actions-history-audit.v2"
NON_FAILURE_CONCLUSIONS = frozenset({"neutral", "skipped"})
UNRESOLVED_BUCKETS = frozenset(
    {"unresolved_ambiguous", "unresolved_cancelled", "unresolved_failure"}
)
DYNAMIC_EVENT = "dynamic"
"""Event of runs that GitHub generates under a generic workflow name."""

_WHERE = r"[a-z][a-z0-9_]* in /\S*"
_DYNAMIC_TITLES = (
    re.compile(rf"(?P<surface>{_WHERE}(?: for \S.*)?) - Update #\d+"),
    re.compile(rf"(?P<surface>(?:Configured )?Graph Update: {_WHERE}) #\d+"),
)
"""The title forms of dynamic runs whose surface is known.

A dependency update names ecosystem, directory and, when it is for named
dependencies, those; a dependency-graph update names ecosystem and directory.
"""


@dataclass(frozen=True)
class WorkflowRun:
    """Fields required to classify one GitHub Actions run."""

    database_id: int
    conclusion: str
    status: str
    workflow_name: str
    workflow_id: int | None
    head_sha: str
    head_branch: str
    created_at: datetime
    event: str
    display_title: str = ""

    @property
    def surface(self) -> str | None:
        """Return what a dynamic run was for, or None when that is not known.

        A run of any other event has the empty surface: its workflow already
        says what it is.
        """
        if self.event != DYNAMIC_EVENT:
            return ""
        for form in _DYNAMIC_TITLES:
            match = form.fullmatch(self.display_title)
            if match is not None:
                return match.group("surface")
        return None

    @property
    def repair_key(self) -> tuple[int, str, str, str] | None:
        """Return what a later success must equal, or None without an identity.

        The key is the workflow's number, the branch, the event and the
        surface. It is None when the workflow number or the surface of a
        dynamic run is unknown; such a run neither is repaired nor repairs.
        """
        surface = self.surface
        if self.workflow_id is None or surface is None:
            return None
        return (self.workflow_id, self.head_branch, self.event, surface)

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
    workflow_id = raw.get("workflowDatabaseId")
    if workflow_id is not None and (
        isinstance(workflow_id, bool) or not isinstance(workflow_id, int) or workflow_id < 1
    ):
        raise ValueError(
            "Workflow run field 'workflowDatabaseId' must be a positive integer or null."
        )
    return WorkflowRun(
        database_id=database_id,
        conclusion=_optional_text(raw, "conclusion"),
        status=_required_text(raw, "status"),
        workflow_name=_required_text(raw, "workflowName"),
        workflow_id=workflow_id,
        head_sha=_required_text(raw, "headSha"),
        head_branch=_required_text(raw, "headBranch"),
        created_at=_parse_timestamp(_required_text(raw, "createdAt")),
        event=_required_text(raw, "event"),
        display_title=_optional_text(raw, "displayTitle"),
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
    key = run.repair_key
    if key is None:
        return False
    return any(
        candidate.repair_key == key
        and candidate.chronology_key > run.chronology_key
        and candidate.status == "completed"
        and candidate.conclusion == "success"
        for candidate in runs
    )


_MATCHED = "A later success exists for the same workflow number, branch, event and surface."


def classify_workflow_runs(
    runs: Sequence[WorkflowRun],
    *,
    excluded_workflows: Sequence[str] = (),
) -> tuple[ClassifiedRun, ...]:
    """Classify runs, resolving bad history only with later matching success.

    Parameters
    ----------
    runs : sequence of WorkflowRun
        The bounded history.
    excluded_workflows : sequence of str
        Workflow names left out, so that the audit cannot latch its own failure.

    Returns
    -------
    tuple of ClassifiedRun
        Every remaining run in chronological order with its bucket and reason.
        A bad run is ``resolved_failure`` or ``superseded_cancelled`` only
        when a later success has the same workflow, branch, event and surface.
    """
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
        elif run.repair_key is None:
            bucket = "unresolved_ambiguous"
            missing = (
                "no workflow number"
                if run.workflow_id is None
                else "a dynamic title of no recognised form"
            )
            reason = (
                f"Conclusion {run.conclusion!r} of a run with {missing}; "
                "no later run can be matched to it."
            )
        elif run.conclusion == "cancelled" and later_success:
            bucket = "superseded_cancelled"
            reason = _MATCHED
        elif later_success:
            bucket = "resolved_failure"
            reason = _MATCHED
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
            "displayTitle": item.run.display_title,
            "event": item.run.event,
            "headBranch": item.run.head_branch,
            "headSha": item.run.head_sha,
            "reason": item.reason,
            "status": item.run.status,
            "surface": item.run.surface,
            "workflowDatabaseId": item.run.workflow_id,
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

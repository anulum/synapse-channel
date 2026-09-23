# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — advisory compute-credit task suggestions
"""Match digest-approved work to private, current compute-credit evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from synapse_channel.core.approvals import STATE_APPROVED, ApprovalReport
from synapse_channel.core.entitlement_view import entitlement_view
from synapse_channel.core.entitlements import (
    COMPUTE_UNITS,
    active_events,
    parse_quantity,
    parse_time,
)
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.ledger import Blackboard

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_REVIEWER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?\Z")
_TASK_FIELDS = frozenset(
    {
        "task_id",
        "project",
        "resource_kind",
        "capability",
        "data_class",
        "unit",
        "required_amount",
        "estimated_total_cost",
        "max_total_cost",
        "cost_currency",
        "price_revision",
        "priority",
        "board_version",
        "authorisation_expires_at",
    }
)


class ComputeCreditError(SynapseError, ValueError):
    """A task or ledger fact cannot support an advisory compute suggestion."""

    code = "compute_credit"


def validate_compute_task(raw: Mapping[str, object]) -> dict[str, object]:
    """Validate one owner-supplied work specification before approval lookup.

    Parameters
    ----------
    raw : Mapping[str, object]
        Exact work, data, resource and total-cost constraints.

    Returns
    -------
    dict[str, object]
        Detached specification suitable for digest binding.

    Raises
    ------
    ComputeCreditError
        If a field, unit or cost constraint is invalid.
    """
    if set(raw) != _TASK_FIELDS:
        raise ComputeCreditError("compute task has missing or unexpected fields")
    for field in (
        "task_id",
        "project",
        "resource_kind",
        "capability",
        "data_class",
        "unit",
        "cost_currency",
        "price_revision",
    ):
        value = raw[field]
        if not isinstance(value, str) or _ID.fullmatch(value) is None:
            raise ComputeCreditError(f"{field} must be a bounded identifier")
    kind = str(raw["resource_kind"])
    if kind not in COMPUTE_UNITS or raw["unit"] not in COMPUTE_UNITS[kind]:
        raise ComputeCreditError("compute task resource kind and unit are incompatible")
    if not isinstance(raw["cost_currency"], str) or raw["cost_currency"] not in {
        "USD",
        "CHF",
        "EUR",
    }:
        raise ComputeCreditError("cost_currency must be USD, CHF or EUR")
    required = parse_quantity(raw["required_amount"], "required_amount")
    if required == 0:
        raise ComputeCreditError("required_amount must be positive")
    estimated = parse_quantity(raw["estimated_total_cost"], "estimated_total_cost")
    maximum = parse_quantity(raw["max_total_cost"], "max_total_cost")
    if estimated > maximum:
        raise ComputeCreditError("estimated total cost exceeds authorised maximum")
    priority = raw["priority"]
    if type(priority) is not int or not 1 <= priority <= 5:
        raise ComputeCreditError("priority must be an integer from 1 to 5")
    board_version = raw["board_version"]
    if type(board_version) is not int or board_version < 1:
        raise ComputeCreditError("board_version must be a positive integer")
    parse_time(raw["authorisation_expires_at"], "authorisation_expires_at")
    return dict(raw)


def approval_subject(task: Mapping[str, object]) -> str:
    """Bind an approval note to every exact task, data and cost constraint.

    Parameters
    ----------
    task : Mapping[str, object]
        Validated candidate work specification.

    Returns
    -------
    str
        Stable SHA-256 approval subject for the canonical JSON task.
    """
    valid = validate_compute_task(task)
    encoded = json.dumps(valid, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "compute-credit:" + hashlib.sha256(encoded).hexdigest()


def suggest_compute_work(
    events: Sequence[Mapping[str, object]],
    tasks: Sequence[Mapping[str, object]],
    approvals: ApprovalReport,
    board: Blackboard,
    *,
    reviewer: str,
    as_of: datetime,
    max_evidence_age_seconds: int = 604800,
) -> dict[str, Any]:
    """Return eligible owner-only suggestions with explicit refusal reasons.

    A matching approved hub note is advisory evidence, never execution or
    spending authority. The caller must name the exact trusted reviewer; a
    changed task specification gets a different approval subject.

    Parameters
    ----------
    events : Sequence[Mapping[str, object]]
        Complete owner-local entitlement ledger history.
    tasks : Sequence[Mapping[str, object]]
        Candidate work specifications from an owner-only file.
    approvals : ApprovalReport
        Replayed approval notes from the same hub as the board.
    board : Blackboard
        Replayed authoritative task board; only ready exact versions qualify.
    reviewer : str
        Exact identity permitted to approve these suggestions.
    as_of : datetime.datetime
        Offset-aware evaluation time.
    max_evidence_age_seconds : int
        Maximum age of account and reconciled balance evidence.

    Returns
    -------
    dict[str, Any]
        JSON-compatible suggestions and explicit excluded-task reasons.

    Raises
    ------
    ComputeCreditError
        If the evaluation clock, reviewer or task batch is invalid.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ComputeCreditError("as_of must include a UTC offset")
    if _REVIEWER.fullmatch(reviewer) is None:
        raise ComputeCreditError("reviewer must be an exact identity")
    if not 0 < max_evidence_age_seconds <= 31 * 86400:
        raise ComputeCreditError("max evidence age must be within 31 days")
    active_events(events)
    view = entitlement_view(events, as_of=as_of, private=True)
    accounts = {item["account_id"]: item for item in view["accounts"]}
    decisions = {status.subject: status for status in approvals.statuses}
    ready = {task.task_id: task for task in board.ready_tasks()}
    suggestions: list[dict[str, object]] = []
    excluded: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in tasks:
        task = validate_compute_task(raw)
        task_id = str(task["task_id"])
        if task_id in seen:
            raise ComputeCreditError("duplicate task id in candidate file")
        seen.add(task_id)
        board_task = ready.get(task_id)
        if (
            board_task is None
            or board_task.project != task["project"]
            or board_task.version != task["board_version"]
        ):
            excluded.append({"task_id": task_id, "reason": "no_matching_ready_board_task"})
            continue
        subject = approval_subject(task)
        status = decisions.get(subject)
        if (
            status is None
            or status.current_state != STATE_APPROVED
            or status.decided_by != reviewer
            or status.decided_at > as_of.timestamp()
            or parse_time(task["authorisation_expires_at"], "authorisation_expires_at") <= as_of
        ):
            excluded.append({"task_id": task_id, "reason": "missing_current_exact_approval"})
            continue
        matches: list[dict[str, object]] = []
        for pool in view["pools"]:
            account = accounts[pool["account_id"]]
            if (
                pool["resource_kind"] != task["resource_kind"]
                or pool["unit"] != task["unit"]
                or task["project"] not in pool["eligible_projects"]
                or task["capability"] not in pool["capabilities"]
                or task["data_class"] not in pool["data_classes"]
            ):
                continue
            account_age = account["record_age_seconds"]
            if (
                not pool["account_usable"]
                or not isinstance(account_age, (int, float))
                or not 0 <= account_age <= max_evidence_age_seconds
            ):
                continue
            for window in pool["windows"]:
                age = window["balance_observation_age_seconds"]
                remaining = window["remaining"]
                if (
                    not window["current"]
                    or window["price_revision"] != task["price_revision"]
                    or window["balance_evidence"] != "observed_plus_recorded_usage"
                    or not isinstance(age, (int, float))
                    or not 0 <= age <= max_evidence_age_seconds
                    or not isinstance(remaining, str)
                    or parse_quantity(remaining, "remaining")
                    < parse_quantity(task["required_amount"], "required_amount")
                ):
                    continue
                matches.append(
                    {
                        "pool_id": pool["pool_id"],
                        "window_id": window["window_id"],
                        "unit": window["unit"],
                        "remaining": remaining,
                        "expires_at": window["ends_at"],
                        "expires_in_seconds": window["expires_in_seconds"],
                        "idle_cost": pool["idle_cost"],
                        "balance_evidence": window["balance_evidence"],
                    }
                )
        if matches:
            matches.sort(key=lambda item: (str(item["expires_at"]), str(item["pool_id"])))
            suggestions.append(
                {
                    "task_id": task_id,
                    "project": task["project"],
                    "priority": task["priority"],
                    "required_amount": task["required_amount"],
                    "estimated_total_cost": task["estimated_total_cost"],
                    "cost_currency": task["cost_currency"],
                    "approval_subject": subject,
                    "options": matches,
                }
            )
        else:
            excluded.append({"task_id": task_id, "reason": "no_current_eligible_pool"})
    suggestions.sort(key=lambda item: (cast(int, item["priority"]), str(item["task_id"])))
    return {
        "authority": "advisory_only",
        "as_of": as_of.isoformat(),
        "reviewer": reviewer,
        "suggestions": suggestions,
        "excluded": excluded,
        "no_job_launched": True,
    }

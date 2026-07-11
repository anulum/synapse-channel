# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only blackboard and claim column projection
"""Project blackboard tasks and live claims into exact-status board columns.

The blackboard and claim registry are separate authorities: the former carries
the shared plan, while the latter carries the live lease and operational status.
This module joins their snapshot rows by task id without mutating either source.
It preserves every real lifecycle value and puts unknown additive values in a
visible fallback column rather than inventing a review stage or hiding work.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from synapse_channel.core.ledger import TERMINAL_LEDGER_STATUSES
from synapse_channel.core.lifecycle import TERMINAL_STATUSES, TaskStatus

JsonDict = dict[str, object]
"""JSON-compatible mapping emitted by the board-column projection."""

COLUMN_SPECS: Final[tuple[tuple[str, str], ...]] = (
    ("open", "Open"),
    ("claimed", "Claimed"),
    ("working", "Working"),
    ("input_required", "Input required"),
    ("blocked", "Blocked"),
    ("closed", "Closed"),
    ("other", "Other"),
)
"""Stable column identifiers and labels in display order."""

TRUST_BOUNDARY: Final = (
    "Read-only projection of blackboard and live claim snapshots; columns do not claim, "
    "assign, reserve, release, approve, or update work."
)
"""Authority boundary carried in every projection response."""


@dataclass(frozen=True)
class _BoardCard:
    """One declared task or ad-hoc claim rendered in a board column.

    Raw board and claim statuses remain separate so contradictory or future
    snapshot values are visible to the operator instead of being flattened away.
    """

    task_id: str
    title: str
    column: str
    board_status: str
    claim_status: str
    status_source: str
    declared: bool
    ready: bool
    depends_on: tuple[str, ...]
    blocked_by: tuple[str, ...]
    unknown_dependencies: tuple[str, ...]
    suggested_owner: str
    owner: str
    lease_expires_at: float | None
    lease_stale: bool
    paths: tuple[str, ...]

    def to_dict(self) -> JsonDict:
        """Return a JSON-compatible card mapping."""
        return {
            "task_id": self.task_id,
            "title": self.title,
            "column": self.column,
            "board_status": self.board_status,
            "claim_status": self.claim_status,
            "status_source": self.status_source,
            "declared": self.declared,
            "ready": self.ready,
            "depends_on": list(self.depends_on),
            "blocked_by": list(self.blocked_by),
            "unknown_dependencies": list(self.unknown_dependencies),
            "suggested_owner": self.suggested_owner,
            "owner": self.owner,
            "lease_expires_at": self.lease_expires_at,
            "lease_stale": self.lease_stale,
            "paths": list(self.paths),
        }


def _mappings(value: object) -> list[Mapping[str, object]]:
    """Return only mapping members from a JSON-like list."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _text(value: object) -> str:
    """Return a stripped string scalar or an empty safe value."""
    return value.strip() if isinstance(value, str) else ""


def _strings(value: object) -> tuple[str, ...]:
    """Return non-empty unique string members in deterministic order."""
    if not isinstance(value, list):
        return ()
    return tuple(sorted({text for item in value if (text := _text(item))}))


def _float_or_none(value: object) -> float | None:
    """Return a finite float for a numeric scalar or numeric string."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _nonnegative_int(value: object, *, fallback: int) -> int:
    """Return a non-negative integer count, never accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return max(value, 0)


def _index_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    """Index the first non-empty row for each task id."""
    indexed: dict[str, Mapping[str, object]] = {}
    for row in rows:
        task_id = _text(row.get("task_id"))
        if task_id:
            indexed.setdefault(task_id, row)
    return indexed


def _dependency_evidence(
    task: Mapping[str, object],
    tasks_by_id: Mapping[str, Mapping[str, object]],
    *,
    ready: bool,
    source_truncated: bool,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return proven blockers and dependencies hidden by a capped snapshot."""
    blocked: list[str] = []
    unknown: list[str] = []
    for dependency in _strings(task.get("depends_on")):
        dependency_row = tasks_by_id.get(dependency)
        if dependency_row is None and source_truncated:
            if not ready:
                unknown.append(dependency)
            continue
        status = _text((dependency_row or {}).get("status"))
        if status not in TERMINAL_LEDGER_STATUSES:
            blocked.append(dependency)
    return tuple(blocked), tuple(unknown)


def _column_for(board_status: str, claim_status: str) -> tuple[str, str]:
    """Return the display column and authoritative status source."""
    if board_status in TERMINAL_LEDGER_STATUSES:
        return "closed", "board"
    if claim_status in TERMINAL_STATUSES:
        return "closed", "claim"
    if board_status == "blocked":
        return "blocked", "board"
    if claim_status == TaskStatus.INPUT_REQUIRED:
        return "input_required", "claim"
    if claim_status == TaskStatus.WORKING:
        return "working", "claim"
    if claim_status == TaskStatus.CLAIMED:
        return "claimed", "claim"
    if claim_status:
        return "other", "claim"
    if board_status == "in_progress":
        return "working", "board"
    if board_status == "open":
        return "open", "board"
    return "other", "board" if board_status else "none"


def _card(
    task_id: str,
    task: Mapping[str, object] | None,
    claim: Mapping[str, object] | None,
    *,
    ready_ids: set[str],
    tasks_by_id: Mapping[str, Mapping[str, object]],
    snapshot_at: float | None,
    source_truncated: bool,
) -> _BoardCard:
    """Build one card by joining optional blackboard and claim rows."""
    task_row: Mapping[str, object] = task or {}
    claim_row: Mapping[str, object] = claim or {}
    board_status = _text(task_row.get("status"))
    claim_status = _text(claim_row.get("status"))
    column, source = _column_for(board_status, claim_status)
    lease_expires_at = _float_or_none(claim_row.get("lease_expires_at"))
    ready = task_id in ready_ids
    blocked_by, unknown_dependencies = (
        _dependency_evidence(
            task_row,
            tasks_by_id,
            ready=ready,
            source_truncated=source_truncated,
        )
        if task is not None
        else ((), ())
    )
    title = _text(task_row.get("title"))
    if not title:
        title = _text(claim_row.get("note"))
    return _BoardCard(
        task_id=task_id,
        title=title,
        column=column,
        board_status=board_status,
        claim_status=claim_status,
        status_source=source,
        declared=task is not None,
        ready=ready,
        depends_on=_strings(task_row.get("depends_on")),
        blocked_by=blocked_by,
        unknown_dependencies=unknown_dependencies,
        suggested_owner=_text(task_row.get("suggested_owner")),
        owner=_text(claim_row.get("owner")),
        lease_expires_at=lease_expires_at,
        lease_stale=(
            lease_expires_at is not None
            and snapshot_at is not None
            and lease_expires_at <= snapshot_at
        ),
        paths=_strings(claim_row.get("paths")),
    )


def build_board_columns(
    board: Mapping[str, object],
    state: Mapping[str, object],
    *,
    now: float | None = None,
) -> JsonDict:
    """Build deterministic board columns from blackboard and claim snapshots.

    Parameters
    ----------
    board : Mapping[str, object]
        Blackboard snapshot containing task rows and ready task ids.
    state : Mapping[str, object]
        Claim-state snapshot containing active claims and its generation time.
    now : float or None, optional
        Explicit lease comparison time. When omitted, the state's finite
        ``generated_at`` value is used. No local clock is consulted.

    Returns
    -------
    dict[str, object]
        Every stable column, joined cards, and source-completeness metadata.
    """
    tasks_by_id = _index_rows(_mappings(board.get("tasks")))
    claims_by_id = _index_rows(_mappings(state.get("active_claims")))
    ready_ids = set(_strings(board.get("ready")))
    source_truncated = board.get("truncated") is True
    snapshot_at = (
        _float_or_none(now) if now is not None else _float_or_none(state.get("generated_at"))
    )
    cards = [
        _card(
            task_id,
            task,
            claims_by_id.get(task_id),
            ready_ids=ready_ids,
            tasks_by_id=tasks_by_id,
            snapshot_at=snapshot_at,
            source_truncated=source_truncated,
        )
        for task_id, task in sorted(tasks_by_id.items())
    ]
    ad_hoc_ids = sorted(set(claims_by_id) - set(tasks_by_id))
    cards.extend(
        _card(
            task_id,
            None,
            claims_by_id[task_id],
            ready_ids=ready_ids,
            tasks_by_id=tasks_by_id,
            snapshot_at=snapshot_at,
            source_truncated=source_truncated,
        )
        for task_id in ad_hoc_ids
    )
    by_column: dict[str, list[_BoardCard]] = {column_id: [] for column_id, _ in COLUMN_SPECS}
    for card in cards:
        by_column[card.column].append(card)
    columns = [
        {
            "id": column_id,
            "label": label,
            "tasks": [
                card.to_dict()
                for card in sorted(by_column[column_id], key=lambda card: card.task_id)
            ],
        }
        for column_id, label in COLUMN_SPECS
    ]
    declared = len(tasks_by_id)
    total_declared = max(
        declared,
        _nonnegative_int(board.get("total_tasks"), fallback=declared),
    )
    return {
        "columns": columns,
        "total_cards": declared + len(ad_hoc_ids),
        "declared_tasks": declared,
        "ad_hoc_claims": len(ad_hoc_ids),
        "source_truncated": source_truncated,
        "total_declared_tasks": total_declared,
        "trust_boundary": TRUST_BOUNDARY,
    }

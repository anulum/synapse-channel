# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared-plan handlers (declare/update task, post progress)
"""Shared-plan handlers writing to the blackboard.

These apply the collaborative plan: declaring or re-declaring a task with its
dependency edges, changing a task's status or suggested owner, and appending a
structured progress note. Each accepted write is journalled (when a durable log
is attached) and broadcast to the channel; a rejected one is privately reported
to the sender.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from synapse_channel.core.atomic_operations import AtomicExecution, OperationDraft
from synapse_channel.core.journal import EventKind, record_ledger_progress, record_ledger_task
from synapse_channel.core.ledger import Blackboard, LedgerTask, ProgressNote
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.task_causality import (
    TaskCausalParent,
    parse_task_causal_parent,
    task_event_payload,
)

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

_JOURNAL_FAILURES = (sqlite3.Error, TypeError, ValueError, OSError)
"""Exception classes a journal append may surface to a planning handler."""

_BoardResult = TypeVar("_BoardResult")


async def _run_board_operation(
    hub: SynapseHub,
    data: dict[str, Any],
    mutate: Callable[[Blackboard], _BoardResult],
    prepare: Callable[[_BoardResult], OperationDraft | None],
    persist: Callable[[_BoardResult], None],
) -> AtomicExecution:
    """Run one board write under the shared mutation actor and optional atomic key."""
    if hub.journal is not None and str(data.get("idem_key") or ""):
        execution = await hub._run_atomic_operation(
            data,
            mutate,
            prepare,
            subject=hub.blackboard,
            publish_candidate=hub.blackboard.publish_from,
        )
        if execution is None:
            raise RuntimeError("durable keyed board operation did not enter atomic mode")
        return execution

    mutation = await hub.state_mutations.run_subject(
        hub.blackboard,
        mutate,
        persist=persist if hub.journal is not None else None,
        publish_candidate=hub.blackboard.publish_from,
    )
    return AtomicExecution("uncommitted", mutation, None)


async def _send_journal_failure(
    hub: SynapseHub,
    websocket: Any,
    *,
    sender: str,
    subject: str,
    exc: BaseException,
) -> None:
    """Report one failed board commit without publishing its private candidate."""
    await hub._send_json(
        websocket,
        hub._system(
            f"{subject} was not journalled ({exc}); mutation rolled back.",
            msg_type=MessageType.ERROR,
            target=sender,
        ),
    )


def _required_atomic_response(execution: AtomicExecution) -> dict[str, Any]:
    """Return an atomic replay/conflict response or fail on an internal invariant breach."""
    if execution.response is None:
        raise RuntimeError(f"atomic {execution.outcome} outcome is missing its response")
    return execution.response


def _expected_version(data: dict[str, Any]) -> tuple[int | None, str | None]:
    """Parse the optional CAS guard, refusing mistyped values.

    Returns ``(version, None)`` when the key is absent, null, or a valid
    integer, and ``(None, reason)`` when the value is present but not an
    integer (booleans included) — a type-confused guard must fail closed at
    ingress rather than silently coerce.
    """
    if "expected_version" not in data or data["expected_version"] is None:
        return None, None
    raw = data["expected_version"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, "Malformed frame: 'expected_version' must be an integer."
    return raw, None


async def _causal_parent(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> tuple[TaskCausalParent | None, bool]:
    """Parse additive task causality metadata and privately reject malformed input."""
    try:
        return parse_task_causal_parent(data.get("causal_parent")), True
    except ValueError as exc:
        await hub._send_json(
            websocket,
            hub._system(
                f"Malformed frame: {exc}",
                msg_type=MessageType.ERROR,
                target=sender,
            ),
        )
        return None, False


async def handle_ledger_task(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Declare or re-declare a plan task and broadcast it, or reject it."""
    task_id = str(data.get("task_id") or "").strip()
    raw_deps = data.get("depends_on")
    depends_on = [str(d) for d in raw_deps] if isinstance(raw_deps, list) else []
    expected, version_error = _expected_version(data)
    if version_error is not None:
        await hub._send_json(
            websocket,
            hub._system(version_error, msg_type=MessageType.ERROR, target=sender),
        )
        return
    causal_parent, parent_valid = await _causal_parent(hub, sender, data, websocket)
    if not parent_valid:
        return

    def mutate(board: Blackboard) -> tuple[bool, str, LedgerTask | None]:
        ok, message = board.post_task(
            task_id=task_id,
            title=str(data.get("title") or ""),
            author=sender,
            description=str(data.get("description") or ""),
            depends_on=depends_on,
            suggested_owner=str(data.get("suggested_owner") or ""),
            project=str(data.get("project") or ""),
            expected_version=expected,
        )
        return ok, message, board.tasks.get(task_id) if ok else None

    def persist(result: tuple[bool, str, LedgerTask | None]) -> None:
        task = result[2]
        if task is not None and hub.journal is not None:
            if causal_parent is None:
                record_ledger_task(hub.journal, task)
            else:
                record_ledger_task(hub.journal, task, causal_parent=causal_parent)

    def prepare(result: tuple[bool, str, LedgerTask | None]) -> OperationDraft | None:
        ok, message, task = result
        if not ok or task is None:
            return None
        posted = hub._system(
            message,
            msg_type=MessageType.LEDGER_TASK_POSTED,
            task=task.as_dict(),
        )
        event_payload = task_event_payload(task.as_dict(), causal_parent)
        return OperationDraft(
            response=posted,
            events=((EventKind.LEDGER_TASK, event_payload),),
            intent={"family": "ledger_task", "response_type": MessageType.LEDGER_TASK_POSTED},
        )

    try:
        execution = await _run_board_operation(hub, data, mutate, prepare, persist)
    except _JOURNAL_FAILURES as exc:
        await _send_journal_failure(
            hub, websocket, sender=sender, subject=f"Task '{task_id}'", exc=exc
        )
        return
    if execution.outcome in {"replayed", "conflict"}:
        await hub._send_json(websocket, _required_atomic_response(execution))
        return
    ok, message, task = execution.mutation
    draft = prepare((ok, message, task))
    if draft is None:
        await hub._send_json(
            websocket, hub._system(message, msg_type=MessageType.ERROR, target=sender)
        )
        return
    posted = execution.response or draft.response
    if execution.outcome == "uncommitted":
        hub._remember(data, posted)
    await hub._broadcast(posted)
    if execution.outcome == "inserted":
        await hub._settle_atomic_operation(data)


async def handle_ledger_task_update(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Apply a plan-status/suggested-owner change and broadcast it, or reject."""
    task_id = str(data.get("task_id") or "").strip()
    status = data.get("status")
    suggested_owner = data.get("suggested_owner")
    project = data.get("project")
    expected, version_error = _expected_version(data)
    if version_error is not None:
        await hub._send_json(
            websocket,
            hub._system(version_error, msg_type=MessageType.ERROR, target=sender),
        )
        return
    causal_parent, parent_valid = await _causal_parent(hub, sender, data, websocket)
    if not parent_valid:
        return

    def mutate(board: Blackboard) -> tuple[bool, str, LedgerTask | None]:
        ok, message = board.update_task(
            task_id,
            status=str(status) if status is not None else None,
            suggested_owner=str(suggested_owner) if suggested_owner is not None else None,
            project=str(project) if project is not None else None,
            expected_version=expected,
        )
        return ok, message, board.tasks.get(task_id) if ok else None

    def persist(result: tuple[bool, str, LedgerTask | None]) -> None:
        task = result[2]
        if task is not None and hub.journal is not None:
            if causal_parent is None:
                record_ledger_task(hub.journal, task)
            else:
                record_ledger_task(hub.journal, task, causal_parent=causal_parent)

    def prepare(result: tuple[bool, str, LedgerTask | None]) -> OperationDraft | None:
        ok, message, task = result
        if not ok or task is None:
            return None
        updated = hub._system(
            message,
            msg_type=MessageType.LEDGER_TASK_UPDATED,
            task=task.as_dict(),
        )
        event_payload = task_event_payload(task.as_dict(), causal_parent)
        return OperationDraft(
            response=updated,
            events=((EventKind.LEDGER_TASK, event_payload),),
            intent={
                "family": "ledger_task_update",
                "response_type": MessageType.LEDGER_TASK_UPDATED,
            },
        )

    try:
        execution = await _run_board_operation(hub, data, mutate, prepare, persist)
    except _JOURNAL_FAILURES as exc:
        await _send_journal_failure(
            hub, websocket, sender=sender, subject=f"Task '{task_id}'", exc=exc
        )
        return
    if execution.outcome in {"replayed", "conflict"}:
        await hub._send_json(websocket, _required_atomic_response(execution))
        return
    ok, message, task = execution.mutation
    draft = prepare((ok, message, task))
    if draft is None:
        await hub._send_json(
            websocket, hub._system(message, msg_type=MessageType.ERROR, target=sender)
        )
        return
    updated = execution.response or draft.response
    if execution.outcome == "uncommitted":
        hub._remember(data, updated)
    await hub._broadcast(updated)
    if execution.outcome == "inserted":
        await hub._settle_atomic_operation(data)


async def handle_ledger_progress(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Append a structured progress note and broadcast it, or reject the kind."""

    def mutate(board: Blackboard) -> tuple[bool, ProgressNote | str]:
        return board.post_progress(
            task_id=str(data.get("task_id") or ""),
            author=sender,
            text=str(data.get("text") or data.get("payload") or ""),
            kind=str(data.get("kind") or "note"),
        )

    def persist(result: tuple[bool, ProgressNote | str]) -> None:
        if result[0] and isinstance(result[1], ProgressNote) and hub.journal is not None:
            record_ledger_progress(hub.journal, result[1])

    def prepare(result: tuple[bool, ProgressNote | str]) -> OperationDraft | None:
        ok, note = result
        if not ok or not isinstance(note, ProgressNote):
            return None
        posted = hub._system(
            f"Progress from {sender}",
            msg_type=MessageType.LEDGER_PROGRESS_POSTED,
            note=note.as_dict(),
        )
        return OperationDraft(
            response=posted,
            events=((EventKind.LEDGER_PROGRESS, note.as_dict()),),
            intent={
                "family": "ledger_progress",
                "response_type": MessageType.LEDGER_PROGRESS_POSTED,
            },
        )

    try:
        execution = await _run_board_operation(hub, data, mutate, prepare, persist)
    except _JOURNAL_FAILURES as exc:
        await _send_journal_failure(hub, websocket, sender=sender, subject="Progress note", exc=exc)
        return
    if execution.outcome in {"replayed", "conflict"}:
        await hub._send_json(websocket, _required_atomic_response(execution))
        return
    ok, result = execution.mutation
    draft = prepare((ok, result))
    if draft is None:
        await hub._send_json(
            websocket, hub._system(str(result), msg_type=MessageType.ERROR, target=sender)
        )
        return
    posted = execution.response or draft.response
    if execution.outcome == "uncommitted":
        hub._remember(data, posted)
    await hub._broadcast(posted)
    if execution.outcome == "inserted":
        await hub._settle_atomic_operation(data)

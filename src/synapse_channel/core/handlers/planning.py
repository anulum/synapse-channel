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

import logging
import sqlite3
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar

from synapse_channel.core.acl import BOARD
from synapse_channel.core.atomic_operations import AtomicExecution, OperationDraft
from synapse_channel.core.handlers.planning_events import TaskEventRecorder
from synapse_channel.core.handlers.verdict_admission import (
    announce_stale_verdicts,
    progress_refusal,
)
from synapse_channel.core.journal import EventKind, record_ledger_progress
from synapse_channel.core.ledger import Blackboard, LedgerTask, ProgressNote
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.task_causality import (
    TaskCausalParent,
    TaskCausalParentInputError,
    parse_task_causal_parent,
)
from synapse_channel.core.verb_access import field_access
from synapse_channel.core.verb_registry import VerbSpec

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.verdict_hub import VerdictHubPolicy

    class PlanningContext(HandlerContext, Protocol):
        """Capabilities consumed by planning handlers and their callees."""

        @property
        def verdict_policy(self) -> VerdictHubPolicy:
            """Return this hub's verdict and correction policy."""
            ...

        @property
        def blackboard(self) -> Blackboard:
            """Return the blackboard used by this handler family."""
            ...


_JOURNAL_FAILURES = (sqlite3.Error, TypeError, ValueError, OSError)
"""Exception classes a journal append may surface to a planning handler."""

_BoardResult = TypeVar("_BoardResult")

logger = logging.getLogger("synapse.hub.planning")


async def _run_board_operation(
    hub: PlanningContext,
    data: dict[str, Any],
    mutate: Callable[[Blackboard], _BoardResult],
    prepare: Callable[[_BoardResult], OperationDraft | None],
    persist: Callable[[_BoardResult], None],
) -> AtomicExecution:
    """Run one board write under the shared mutation actor and optional atomic key."""
    if hub.journal is not None and str(data.get("idem_key") or ""):
        execution = await hub.run_atomic_operation(
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
    hub: PlanningContext,
    websocket: Any,
    *,
    sender: str,
    subject: str,
    exc: BaseException,
) -> None:
    """Log storage diagnostics and privately report rollback without exception text."""
    logger.error(
        "%s was not journalled; mutation rolled back.",
        subject,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    await hub.send_json(
        websocket,
        hub.system(
            f"{subject} was not journalled; mutation rolled back.",
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
    hub: PlanningContext, sender: str, data: dict[str, Any], websocket: Any
) -> tuple[TaskCausalParent | None, bool]:
    """Privately echo authored parent refusals and log unexpected parser faults."""
    try:
        return parse_task_causal_parent(data.get("causal_parent")), True
    except TaskCausalParentInputError as exc:
        reason = f"Malformed frame: {exc}"
    except Exception:
        logger.exception("Unexpected causal parent validation failure")
        reason = "Causal parent validation failed; task was not changed."
    await hub.send_json(
        websocket,
        hub.system(reason, msg_type=MessageType.ERROR, target=sender),
    )
    return None, False


async def handle_ledger_task(
    hub: PlanningContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Declare or re-declare a plan task and broadcast it, or reject it."""
    task_id = str(data.get("task_id") or "").strip()
    raw_deps = data.get("depends_on")
    depends_on = [str(d) for d in raw_deps] if isinstance(raw_deps, list) else []
    expected, version_error = _expected_version(data)
    if version_error is not None:
        await hub.send_json(
            websocket,
            hub.system(version_error, msg_type=MessageType.ERROR, target=sender),
        )
        return
    causal_parent, parent_valid = await _causal_parent(hub, sender, data, websocket)
    if not parent_valid:
        return

    events = TaskEventRecorder(sender, causal_parent)

    def mutate(board: Blackboard) -> tuple[bool, str, LedgerTask | None]:
        events.capture_owner(board, task_id)
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
            events.record(hub.journal, task)

    prepare = partial(
        events.draft, hub=hub, msg_type=MessageType.LEDGER_TASK_POSTED, family="ledger_task"
    )

    try:
        execution = await _run_board_operation(hub, data, mutate, prepare, persist)
    except _JOURNAL_FAILURES as exc:
        await _send_journal_failure(
            hub, websocket, sender=sender, subject=f"Task '{task_id}'", exc=exc
        )
        return
    if execution.outcome in {"replayed", "conflict"}:
        await hub.send_json(websocket, _required_atomic_response(execution))
        return
    ok, message, task = execution.mutation
    draft = prepare((ok, message, task))
    if draft is None:
        await hub.send_json(
            websocket, hub.system(message, msg_type=MessageType.ERROR, target=sender)
        )
        return
    posted = execution.response or draft.response
    if execution.outcome == "uncommitted":
        hub.remember(data, posted)
    await hub.broadcast(posted)
    if execution.outcome == "inserted":
        await hub.settle_atomic_operation(data)


async def handle_ledger_task_update(
    hub: PlanningContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Apply a plan-status/suggested-owner change and broadcast it, or reject."""
    task_id = str(data.get("task_id") or "").strip()
    status = data.get("status")
    suggested_owner = data.get("suggested_owner")
    project = data.get("project")
    expected, version_error = _expected_version(data)
    if version_error is not None:
        await hub.send_json(
            websocket,
            hub.system(version_error, msg_type=MessageType.ERROR, target=sender),
        )
        return
    causal_parent, parent_valid = await _causal_parent(hub, sender, data, websocket)
    if not parent_valid:
        return

    events = TaskEventRecorder(sender, causal_parent)

    def mutate(board: Blackboard) -> tuple[bool, str, LedgerTask | None]:
        events.capture_owner(board, task_id)
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
            events.record(hub.journal, task)

    prepare = partial(
        events.draft,
        hub=hub,
        msg_type=MessageType.LEDGER_TASK_UPDATED,
        family="ledger_task_update",
    )

    try:
        execution = await _run_board_operation(hub, data, mutate, prepare, persist)
    except _JOURNAL_FAILURES as exc:
        await _send_journal_failure(
            hub, websocket, sender=sender, subject=f"Task '{task_id}'", exc=exc
        )
        return
    if execution.outcome in {"replayed", "conflict"}:
        await hub.send_json(websocket, _required_atomic_response(execution))
        return
    ok, message, task = execution.mutation
    draft = prepare((ok, message, task))
    if draft is None:
        await hub.send_json(
            websocket, hub.system(message, msg_type=MessageType.ERROR, target=sender)
        )
        return
    updated = execution.response or draft.response
    if execution.outcome == "uncommitted":
        hub.remember(data, updated)
    await hub.broadcast(updated)
    if execution.outcome == "inserted":
        await hub.settle_atomic_operation(data)


async def handle_ledger_progress(
    hub: PlanningContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Append a structured progress note and broadcast it, or reject the kind.

    Evidence, verdict and correction notes, and approvals of SHA-256 subjects,
    pass the verdict admission first; a refused note is never appended.
    """
    task_id = str(data.get("task_id") or "")
    text = str(data.get("text") or data.get("payload") or "")
    kind = str(data.get("kind") or "note")
    refusal, recorded = progress_refusal(hub, sender=sender, task_id=task_id, kind=kind, text=text)
    if refusal is not None:
        await hub.send_json(
            websocket, hub.system(refusal, msg_type=MessageType.ERROR, target=sender)
        )
        return

    def mutate(board: Blackboard) -> tuple[bool, ProgressNote | str]:
        return board.post_progress(task_id=task_id, author=sender, text=text, kind=kind)

    def persist(result: tuple[bool, ProgressNote | str]) -> None:
        if result[0] and isinstance(result[1], ProgressNote) and hub.journal is not None:
            record_ledger_progress(hub.journal, result[1])

    def prepare(result: tuple[bool, ProgressNote | str]) -> OperationDraft | None:
        ok, note = result
        if not ok or not isinstance(note, ProgressNote):
            return None
        posted = hub.system(
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
        await hub.send_json(websocket, _required_atomic_response(execution))
        return
    ok, result = execution.mutation
    draft = prepare((ok, result))
    if draft is None:
        await hub.send_json(
            websocket, hub.system(str(result), msg_type=MessageType.ERROR, target=sender)
        )
        return
    await _publish_progress(hub, data, execution, execution.response or draft.response)
    # Only an appended note reaches this point (replays and conflicts returned above).
    await announce_stale_verdicts(hub, recorded, sender=sender, kind=kind, text=text)


async def _publish_progress(
    hub: PlanningContext, data: dict[str, Any], execution: AtomicExecution, posted: dict[str, Any]
) -> None:
    """Remember an unkeyed reply, broadcast the posted note and settle a keyed operation."""
    if execution.outcome == "uncommitted":
        hub.remember(data, posted)
    await hub.broadcast(posted)
    if execution.outcome == "inserted":
        await hub.settle_atomic_operation(data)


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.LEDGER_TASK,),
        handler=handle_ledger_task,
        reply_types=(MessageType.LEDGER_TASK_POSTED,),
        mutates=True,
        replay_protected=True,
        mutation_guarded=True,
        accesses=field_access(BOARD, "board", "task_id", fallback="*"),
        event_kinds=(EventKind.LEDGER_TASK,),
        minimum_wire_version=1,
        commands=("task declare",),
    ),
    VerbSpec(
        request_types=(MessageType.LEDGER_TASK_UPDATE,),
        handler=handle_ledger_task_update,
        reply_types=(MessageType.LEDGER_TASK_UPDATED,),
        mutates=True,
        replay_protected=True,
        mutation_guarded=True,
        accesses=field_access(BOARD, "board", "task_id", fallback="*"),
        event_kinds=(EventKind.LEDGER_TASK,),
        minimum_wire_version=1,
        commands=("task update",),
    ),
    VerbSpec(
        request_types=(MessageType.LEDGER_PROGRESS,),
        handler=handle_ledger_progress,
        reply_types=(MessageType.LEDGER_PROGRESS_POSTED,),
        mutates=True,
        replay_protected=True,
        mutation_guarded=True,
        accesses=field_access(BOARD, "board", "task_id", fallback="*"),
        event_kinds=(EventKind.LEDGER_PROGRESS,),
        minimum_wire_version=1,
        commands=("task progress",),
    ),
)

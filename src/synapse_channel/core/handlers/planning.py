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
from collections.abc import Callable, Mapping
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar

from synapse_channel.core.acl import BOARD
from synapse_channel.core.atomic_operations import AtomicExecution, OperationDraft
from synapse_channel.core.handlers.approval_load_admission import (
    ApprovalLoadAdmission,
    approval_load_admission,
)
from synapse_channel.core.handlers.planning_events import TaskEventRecorder
from synapse_channel.core.handlers.verdict_admission import (
    announce_stale_verdicts,
    ladder_recheck,
    progress_refusal,
)
from synapse_channel.core.journal import (
    EventKind,
    ledger_progress_events,
    record_ledger_progress,
    record_ledger_progress_admitted,
)
from synapse_channel.core.ledger import (
    Blackboard,
    LedgerTask,
    ProgressNote,
    progress_note_refusal,
)
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

    from synapse_channel.core.approval_load import ApprovalLoad
    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.operation_records import OperationLedger
    from synapse_channel.core.role_grants import RoleGrants
    from synapse_channel.core.verdict_hub import VerdictHubPolicy

    class PlanningContext(HandlerContext, Protocol):
        """Capabilities consumed by planning handlers and their callees."""

        @property
        def verdict_policy(self) -> VerdictHubPolicy:
            """Return this hub's verdict and correction policy."""
            ...

        @property
        def operation_records(self) -> OperationLedger:
            """Return the declared operations, their attempts and the stops."""
            ...

        @property
        def role_grants(self) -> RoleGrants | None:
            """Return the hub's role grants, which tell a person from an automated role."""
            ...

        @property
        def blackboard(self) -> Blackboard:
            """Return the blackboard used by this handler family."""
            ...

        @property
        def ladder_enabled(self) -> bool:
            """Return whether ``--ladder`` refuses an automated approval at A1."""
            ...

        @property
        def approval_load(self) -> ApprovalLoad | None:
            """Return the hub's approval-load state, or ``None`` with no policy declared."""
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


def _declare_task(
    board: Blackboard,
    events: TaskEventRecorder,
    sender: str,
    data: dict[str, Any],
    task_id: str,
    depends_on: list[str],
    expected: int | None,
) -> tuple[bool, str, LedgerTask | None]:
    """Declare the task, using ``default_project`` only when it is created."""
    events.capture_owner(board, task_id)
    ok, message = board.post_task(
        task_id=task_id,
        title=str(data.get("title") or ""),
        author=sender,
        description=str(data.get("description") or ""),
        depends_on=depends_on,
        suggested_owner=str(data.get("suggested_owner") or ""),
        project=str(data.get("project") or ""),
        default_project=str(data.get("default_project") or ""),
        expected_version=expected,
    )
    return ok, message, board.tasks.get(task_id) if ok else None


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
        return _declare_task(board, events, sender, data, task_id, depends_on, expected)

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

    A note longer than :data:`~synapse_channel.core.ledger.MAX_PROGRESS_NOTE_BYTES`
    is refused before anything is read or appended, never trimmed. Evidence,
    verdict and correction notes, and approvals of SHA-256 subjects, pass the
    verdict admission first; a refused note is never appended.
    """
    task_id = str(data.get("task_id") or "")
    text = str(data.get("text") or data.get("payload") or "")
    kind = str(data.get("kind") or "note")
    too_long = progress_note_refusal(text)
    if too_long is not None:
        await hub.send_json(
            websocket, hub.system(too_long, msg_type=MessageType.ERROR, target=sender)
        )
        return
    refusal, recorded = progress_refusal(hub, sender=sender, task_id=task_id, kind=kind, text=text)
    admission: ApprovalLoadAdmission | None = None
    if refusal is None:
        try:
            refusal, admission = approval_load_admission(
                hub, sender=sender, task_id=task_id, kind=kind, text=text
            )
        except _JOURNAL_FAILURES as exc:
            # The policy row or a refusal row could not be written: nothing was
            # counted and no note was appended, so answer like any journal failure.
            await _send_journal_failure(
                hub, websocket, sender=sender, subject="Approval request", exc=exc
            )
            return
    if refusal is not None:
        await hub.send_json(
            websocket, hub.system(refusal, msg_type=MessageType.ERROR, target=sender)
        )
        return
    appended: tuple[AtomicExecution, dict[str, Any]] | None = None
    try:
        appended = await _append_progress(
            hub,
            data,
            websocket,
            sender=sender,
            task_id=task_id,
            kind=kind,
            text=text,
            admitted=None if admission is None else admission.row(),
        )
    finally:
        # The admitted row was written with the note, in one transaction; a note
        # that was not appended gives its reserved place back.
        if admission is not None and appended is None:
            admission.not_appended()
    if appended is None:
        return
    execution, posted = appended
    await _publish_progress(hub, data, execution, posted)
    # Only an appended note reaches this point (replays and conflicts were not appended).
    await announce_stale_verdicts(hub, recorded, sender=sender, kind=kind, text=text)


async def _append_progress(
    hub: PlanningContext,
    data: dict[str, Any],
    websocket: Any,
    *,
    sender: str,
    task_id: str,
    kind: str,
    text: str,
    admitted: Mapping[str, Any] | None = None,
) -> tuple[AtomicExecution, dict[str, Any]] | None:
    """Append one admitted progress note, with its approval-load row in the same transaction.

    Returns
    -------
    tuple[AtomicExecution, dict[str, Any]] or None
        The execution and the reply to publish when the note was appended, or
        ``None`` when it was not (refused, replayed, conflicting, or lost to a
        journal failure); the sender has then been answered.
    """

    def mutate(board: Blackboard) -> tuple[bool, ProgressNote | str]:
        return _post_or_refuse(hub, board, sender=sender, task_id=task_id, kind=kind, text=text)

    def persist(result: tuple[bool, ProgressNote | str]) -> None:
        if result[0] and isinstance(result[1], ProgressNote) and hub.journal is not None:
            if admitted is None:
                record_ledger_progress(hub.journal, result[1])
            else:
                record_ledger_progress_admitted(hub.journal, result[1], admitted)

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
            events=ledger_progress_events(note, admitted),
            intent={
                "family": "ledger_progress",
                "response_type": MessageType.LEDGER_PROGRESS_POSTED,
            },
        )

    try:
        execution = await _run_board_operation(hub, data, mutate, prepare, persist)
    except _JOURNAL_FAILURES as exc:
        await _send_journal_failure(hub, websocket, sender=sender, subject="Progress note", exc=exc)
        return None
    if execution.outcome in {"replayed", "conflict"}:
        await hub.send_json(websocket, _required_atomic_response(execution))
        return None
    ok, result = execution.mutation
    draft = prepare((ok, result))
    if draft is None:
        await hub.send_json(
            websocket, hub.system(str(result), msg_type=MessageType.ERROR, target=sender)
        )
        return None
    return execution, execution.response or draft.response


def _post_or_refuse(
    hub: PlanningContext, board: Blackboard, *, sender: str, task_id: str, kind: str, text: str
) -> tuple[bool, ProgressNote | str]:
    """Post a note under the state actor's lock, unless ``--ladder`` refuses it now."""
    late = ladder_recheck(hub, sender=sender, kind=kind, text=text)
    if late is not None:
        return False, late
    return board.post_progress(task_id=task_id, author=sender, text=text, kind=kind)


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

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable A2A forwarding order
"""Persist an exchange before forwarding and retain unknown results for inquiry."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_errors import A2ANotFoundError, A2AStoreError
from synapse_channel.a2a_outcome import begin_execution
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.a2a_task_flow import build_continued_task, stored_task_target
from synapse_channel.a2a_validation import TERMINAL_TASK_STATES


class ForwardMessage(Protocol):
    """Send exactly one message through the bridge's real agent runtime."""

    def __call__(
        self,
        message: JsonMap,
        *,
        task_id: str,
        context_id: str,
        target: str,
        operation_timeout_seconds: float | None = None,
    ) -> None:
        """Forward one message or report an unconfirmed exchange."""


class WriteStatus(Protocol):
    """Commit an existing protocol transition and its execution observation."""

    def __call__(
        self,
        task: JsonMap,
        *,
        state: str,
        message: JsonMap | None = None,
        publish: bool = True,
    ) -> JsonMap:
        """Persist one task update before publishing it."""


class PublishTask(Protocol):
    """Publish a committed task snapshot without requiring an external webhook."""

    def __call__(self, task: JsonMap, *, deliver_push: bool = True) -> None:
        """Publish one task snapshot."""


@dataclass(frozen=True)
class PreparedExchange:
    """A durably recorded working task waiting for its single hand-off.

    Attributes
    ----------
    task : JsonMap
        Committed working task.
    message : JsonMap
        Message to forward (the continued message for an existing task).
    target : str
        SYNAPSE target of the hand-off.
    """

    task: JsonMap
    message: JsonMap
    target: str


class A2AExecution:
    """Own write-before-send ordering without assuming cancellation stopped work."""

    def __init__(
        self,
        store: A2ATaskStore,
        forward: ForwardMessage,
        write_status: WriteStatus,
        publish: PublishTask,
        transition_lock: AbstractContextManager[object],
    ) -> None:
        """Bind the existing store and bridge's supported forwarding callbacks."""
        self.store = store
        self.forward = forward
        self.write_status = write_status
        self.publish = publish
        self.transition_lock = transition_lock

    def send(
        self,
        task: JsonMap,
        message: JsonMap,
        *,
        target: str,
        operation_timeout_seconds: float | None = None,
        continue_existing: bool = False,
    ) -> JsonMap:
        """Durably prepare one exchange, then forward without mutation replay.

        Persistence failures occur before the send. After invocation starts,
        failures retain an unknown result under the original task id. A peer
        reply received while sending is in flight must not be overwritten by a
        later local working state.
        """
        prepared = self.prepare(task, message, target=target, continue_existing=continue_existing)
        return self.forward_prepared(prepared, operation_timeout_seconds=operation_timeout_seconds)

    def prepare(
        self,
        task: JsonMap,
        message: JsonMap,
        *,
        target: str,
        continue_existing: bool = False,
    ) -> PreparedExchange:
        """Durably record the working state of one exchange before any hand-off.

        Parameters
        ----------
        task : JsonMap
            New task, or the request's view of an existing task to continue.
        message : JsonMap
            Validated user message to forward.
        target : str
            Default SYNAPSE target.
        continue_existing : bool, optional
            Continue the stored task under A2A 1.0 task-id semantics.

        Returns
        -------
        PreparedExchange
            The committed working task with the message and target to forward.

        Raises
        ------
        A2ANotFoundError
            When an existing task to continue is no longer stored.
        """
        with self.transition_lock:
            if continue_existing:
                current = self.store.get(str(task["id"]))
                if current is None:
                    raise A2ANotFoundError(f"Unknown task: {task['id']}")
                task, message = build_continued_task(current, message)
                target = stored_task_target(current, default=target)
            prepared = begin_execution(task, message)
            stored = self.write_status(
                prepared,
                state="TASK_STATE_WORKING",
                message=prepared["status"].get("message"),
                publish=False,
            )
            self.publish(stored, deliver_push=False)
        return PreparedExchange(stored, message, target)

    def forward_prepared(
        self, prepared: PreparedExchange, *, operation_timeout_seconds: float | None = None
    ) -> JsonMap:
        """Hand one prepared exchange to SYNAPSE without holding any bridge lock.

        Parameters
        ----------
        prepared : PreparedExchange
            Result of :meth:`prepare`.
        operation_timeout_seconds : float or None, optional
            Caller deadline for the hand-off.

        Returns
        -------
        JsonMap
            The task as stored after the hand-off, which may already carry a reply.

        Raises
        ------
        A2AStoreError
            When the task state vanished after forwarding; the outcome is unknown.
        Exception
            The forwarding failure, after a non-terminal task is marked failed.
        """
        stored, message, target = prepared.task, prepared.message, prepared.target
        task_id = str(stored["id"])
        try:
            self.forward(
                message,
                task_id=task_id,
                context_id=str(stored["contextId"]),
                target=target,
                operation_timeout_seconds=operation_timeout_seconds,
            )
        except Exception:
            with self.transition_lock:
                current = self.store.get(task_id)
                if current is not None:
                    state = current.get("status", {}).get("state")
                    if state == "TASK_STATE_COMPLETED":
                        return current
                    if state not in TERMINAL_TASK_STATES:
                        self.write_status(current, state="TASK_STATE_FAILED")
            raise
        current = self.store.get(task_id)
        if current is None:
            raise A2AStoreError("forwarded task state unavailable; outcome unknown")
        return current

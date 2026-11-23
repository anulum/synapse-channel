# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent2Agent frame correlation
"""Correlate SYNAPSE replies with the A2A tasks that asked for them.

One correlation lock is shared with execution and with expiry. Per-target
locks keep hand-offs to one sender ordered without blocking other targets.
"""

from __future__ import annotations

import threading
import uuid
from contextlib import AbstractContextManager
from typing import Any

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_execution import WriteStatus
from synapse_channel.a2a_outcome import (
    OPERATION_KEY,
    bind_reply,
    execution_id,
)
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.a2a_task_flow import render_message_text
from synapse_channel.a2a_validation import TERMINAL_TASK_STATES, validate_bridge_id

A2A_METADATA_TASK_ID = "a2aTaskId"
"""Chat metadata key carrying the bridge task id."""

A2A_METADATA_CONTEXT_ID = "a2aContextId"
"""Chat metadata key carrying the bridge context id."""


def _valid_metadata_id(value: object) -> str | None:
    """Return ``value`` as a bridge-safe id, or ``None`` when unusable."""
    if value is None:
        return None
    candidate = str(value)
    try:
        validate_bridge_id(candidate, field="metadata")
    except ValueError:
        return None
    return candidate


def _a2a_metadata_correlation(data: JsonMap) -> tuple[str | None, str | None, bool]:
    """Extract trusted A2A correlation fields from structured chat metadata.

    The boolean is ``True`` only when the frame attempted A2A correlation. A
    malformed attempt is rejected by the caller instead of falling back to text or
    sender inference, which keeps user-controlled chat content from selecting a
    task.
    """
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return None, None, False
    if A2A_METADATA_TASK_ID not in metadata and A2A_METADATA_CONTEXT_ID not in metadata:
        return None, None, False
    return (
        _valid_metadata_id(metadata.get(A2A_METADATA_TASK_ID)),
        _valid_metadata_id(metadata.get(A2A_METADATA_CONTEXT_ID)),
        True,
    )


class _TargetLocks:
    """One lock per SYNAPSE target, created on first use."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def for_target(self, target: str) -> threading.Lock:
        """Return the lock that orders hand-offs to ``target``."""
        with self._guard:
            return self._locks.setdefault(target, threading.Lock())


class A2AFrameCorrelator:
    """Own fallback correlation, the shared lock, and inbound frame matching."""

    def __init__(
        self,
        *,
        agent: Any,
        store: A2ATaskStore,
        run: Any,
        write_status: WriteStatus,
    ) -> None:
        self.agent = agent
        self._store = store
        self._run = run
        self._write_status = write_status
        self._pending: dict[str, list[str]] = {}
        self._target_locks = _TargetLocks()
        self._lock = threading.RLock()

    @property
    def lock(self) -> AbstractContextManager[object]:
        """Return the one lock execution and expiry take."""
        return self._lock

    def forward(
        self,
        message: JsonMap,
        *,
        task_id: str,
        context_id: str,
        target: str,
        operation_timeout_seconds: float | None = None,
    ) -> None:
        """Forward one task-bound message and maintain fallback correlation."""
        with self._target_locks.for_target(target):
            pending = self._pending.setdefault(target, [])
            if task_id not in pending:
                pending.append(task_id)
            text = render_message_text(message)
            if text:
                self._run(
                    self.agent.chat(
                        text,
                        target=target,
                        metadata={
                            A2A_METADATA_TASK_ID: task_id,
                            A2A_METADATA_CONTEXT_ID: context_id,
                            OPERATION_KEY: execution_id(
                                {"id": task_id, "contextId": context_id}, message
                            ),
                        },
                    ),
                    timeout_seconds=operation_timeout_seconds,
                )

    def remove_pending(self, task_id: str) -> None:
        """Remove ``task_id`` from all pending fallback correlation lists."""
        for target, pending in list(self._pending.items()):
            self._pending[target] = [stored for stored in pending if stored != task_id]
            if not self._pending[target]:
                del self._pending[target]

    def pending_for_sender(self, sender: str) -> str | None:
        """Return the oldest pending task for one SYNAPSE sender."""
        pending = self._pending.get(sender)
        if not pending:
            return None
        while pending:
            task_id = pending.pop(0)
            if self._store.get(task_id) is not None:
                return task_id
        del self._pending[sender]
        return None

    def sender_matches(self, task: JsonMap, sender: str) -> bool:
        """Return whether ``sender`` is allowed to complete ``task``."""
        metadata = task.get("metadata")
        target = ""
        if isinstance(metadata, dict):
            target = str(metadata.get("synapseTarget") or "")
        return target in {"", "all", sender}

    def handle_frame(self, data: JsonMap) -> None:
        """Correlate an inbound SYNAPSE chat frame to an open A2A task."""
        with self._lock:
            if data.get("type") != "chat":
                return
            payload = str(data.get("payload", ""))
            sender = str(data.get("sender", ""))

            task_id, context_id, has_correlation_metadata = _a2a_metadata_correlation(data)
            if has_correlation_metadata and (task_id is None or context_id is None):
                return
            if task_id is None and sender in self._pending:
                task_id = self.pending_for_sender(sender)

            if not task_id:
                return
            task = self._store.get(task_id)
            if task is None or not self.sender_matches(task, sender):
                return
            if has_correlation_metadata and str(task.get("contextId", "")) != context_id:
                return
            status = task.get("status", {})
            if isinstance(status, dict) and status.get("state") in TERMINAL_TASK_STATES:
                return

            task = bind_reply(task, data)
            if task is None:
                return

            reply_text = payload.strip()
            reply_part: JsonMap = {
                "messageId": str(uuid.uuid4()),
                "role": "ROLE_AGENT",
                "parts": [{"text": reply_text, "mediaType": "text/plain"}],
            }
            task["history"] = [*task.get("history", []), reply_part]
            task["artifacts"] = list(task.get("artifacts", []))
            task["artifacts"].append(
                {
                    "artifactId": f"synapse-reply-{task_id}",
                    "name": "SYNAPSE reply",
                    "description": f"Correlated reply from {sender}",
                    "parts": [{"text": reply_text, "mediaType": "text/plain"}],
                }
            )
            self.remove_pending(task_id)
            self._write_status(task, state="TASK_STATE_COMPLETED", message=reply_part)

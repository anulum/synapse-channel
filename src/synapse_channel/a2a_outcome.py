# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A execution observations
"""Keep local A2A task state separate from remote effect and stop confirmation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from synapse_channel.execution_outcome import ExecutionOutcome, OutcomeState

OUTCOME_KEY = "synapseExecutionOutcome"
"""Additive A2A task metadata field shared by HTTP, RPC, SSE and durable state."""

OPERATION_KEY = "a2aOperationId"
"""Opaque exchange identity echoed by a peer to distinguish continuation replies."""


def execution_id(task: Mapping[str, Any], message: Mapping[str, Any] | None = None) -> str:
    """Derive a stable bounded id without exposing message content."""
    identity = [task.get("id"), task.get("contextId")]
    if message is not None:
        identity.append(message.get("messageId"))
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return "a2a-" + hashlib.sha256(encoded).hexdigest()


def begin_execution(task: Mapping[str, Any], message: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached task whose next exchange has not yet been invoked."""
    result = dict(task)
    metadata = dict(task.get("metadata") or {})
    metadata[OUTCOME_KEY] = ExecutionOutcome(
        execution_id(task, message),
        "a2a_exchange",
        OutcomeState.NOT_STARTED,
        "request_not_invoked",
        external_effect=OutcomeState.NOT_STARTED,
    ).to_json()
    result["metadata"] = metadata
    return result


def observed_task(task: Mapping[str, Any], state: OutcomeState, observation: str) -> dict[str, Any]:
    """Return an updated observation without mutating the store's prior record."""
    result = dict(task)
    metadata = dict(task.get("metadata") or {})
    previous = metadata.get(OUTCOME_KEY)
    identity = previous.get("operation_id") if isinstance(previous, dict) else None
    if not isinstance(identity, str) or not identity.startswith("a2a-") or len(identity) != 68:
        identity = execution_id(task)
    metadata[OUTCOME_KEY] = ExecutionOutcome(identity, "a2a_exchange", state, observation).to_json()
    result["metadata"] = metadata
    return result


def bind_reply(task: Mapping[str, Any], frame: Mapping[str, Any]) -> dict[str, Any] | None:
    """Detach a matching reply observation; reject a different exchange identity."""
    metadata = dict(task.get("metadata") or {})
    previous = metadata.get(OUTCOME_KEY)
    expected = previous.get("operation_id") if isinstance(previous, dict) else None
    frame_metadata = frame.get("metadata")
    operation = frame_metadata.get(OPERATION_KEY) if isinstance(frame_metadata, dict) else None
    if operation is not None and operation != expected:
        return None
    metadata["synapseReplyOperationId"] = operation
    return {**task, "metadata": metadata}


def protocol_observation(task: Mapping[str, Any], protocol_state: str) -> dict[str, Any]:
    """Report exactly what a local protocol transition establishes.

    A completed exchange confirms receipt of the correlated reply, not its
    claimed business postcondition. Local cancellation does not send a stop to
    the peer. Failure/expiry cannot establish that the remote work failed.
    """
    state, observation = {
        "TASK_STATE_WORKING": (OutcomeState.STARTED, "forward_intent_prepared"),
        "TASK_STATE_COMPLETED": (OutcomeState.CONFIRMED_DONE, "correlated_reply_received"),
        "TASK_STATE_FAILED": (OutcomeState.UNKNOWN, "local_exchange_failed"),
        "TASK_STATE_CANCELED": (OutcomeState.UNKNOWN, "local_task_cancelled"),
    }.get(protocol_state, (OutcomeState.UNKNOWN, "local_protocol_transition"))
    if protocol_state == "TASK_STATE_COMPLETED":
        metadata = task.get("metadata") or {}
        previous = metadata.get(OUTCOME_KEY) or {}
        if metadata.get("synapseReplyOperationId") != previous.get("operation_id"):
            state, observation = OutcomeState.UNKNOWN, "reply_operation_unconfirmed"
    return observed_task(task, state, observation)

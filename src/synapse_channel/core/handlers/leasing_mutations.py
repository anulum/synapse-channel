# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — lease state mutation callbacks
"""State-only lease mutation callbacks supplied to the authoritative actor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from synapse_channel.core.numeric_coercion import optional_int_field
from synapse_channel.core.state import SynapseState

if TYPE_CHECKING:
    from synapse_channel.core.state_models import TaskClaim


def update_task(
    state: SynapseState,
    *,
    sender: str,
    task_id: str,
    status: Any,
    note: Any,
    data_ref: Any,
    data: dict[str, Any],
) -> tuple[bool, str, TaskClaim | None]:
    """Apply the owner fields and return the changed lease when granted.

    Parameters
    ----------
    state
        Candidate state supplied by the serialised mutation runner.
    sender
        Authenticated owner of this request.
    task_id
        Task identifier already normalised by the handler.
    status
        Original optional status field.
    note
        Original optional note field.
    data_ref
        Original optional data reference.
    data
        Original request with optional fencing or receipt fields.

    Returns
    -------
    tuple
        The original state mutation result.
    """
    ok, message = state.update_task(
        sender,
        task_id,
        status=str(status) if status else None,
        note=str(note) if note is not None else None,
        data_ref=str(data_ref) if data_ref is not None else None,
        epoch=optional_int_field(data, "epoch"),
        expected_version=optional_int_field(data, "expected_version"),
    )
    return ok, message, state.claims.get(task_id) if ok else None


def release_task(
    state: SynapseState,
    *,
    sender: str,
    task_id: str,
    data: dict[str, Any],
) -> tuple[bool, str]:
    """Release the task under the request fencing epoch.

    Parameters
    ----------
    state
        Candidate state supplied by the serialised mutation runner.
    sender
        Authenticated owner of this request.
    task_id
        Task identifier already normalised by the handler.
    data
        Original request with optional fencing or receipt fields.

    Returns
    -------
    tuple
        The original state mutation result.
    """
    return state.release(sender, task_id, epoch=optional_int_field(data, "epoch"))


def handoff_task(
    state: SynapseState,
    *,
    sender: str,
    task_id: str,
    to_agent: str,
    note: Any,
    data: dict[str, Any],
) -> tuple[bool, str, TaskClaim | None]:
    """Transfer the lease under the request fencing epoch.

    Parameters
    ----------
    state
        Candidate state supplied by the serialised mutation runner.
    sender
        Authenticated owner of this request.
    task_id
        Task identifier already normalised by the handler.
    to_agent
        Recipient already checked for online presence.
    note
        Original optional note field.
    data
        Original request with optional fencing or receipt fields.

    Returns
    -------
    tuple
        The original state mutation result.
    """
    ok, message = state.handoff(
        sender,
        task_id,
        to_agent,
        note=str(note) if note is not None else None,
        epoch=optional_int_field(data, "epoch"),
    )
    return ok, message, state.claims.get(task_id) if ok else None


def save_checkpoint(
    state: SynapseState,
    *,
    sender: str,
    task_id: str,
    checkpoint: str,
    data: dict[str, Any],
) -> tuple[bool, str, TaskClaim | None]:
    """Save the resume token under the request fencing epoch.

    Parameters
    ----------
    state
        Candidate state supplied by the serialised mutation runner.
    sender
        Authenticated owner of this request.
    task_id
        Task identifier already normalised by the handler.
    checkpoint
        Resume token parsed by the handler.
    data
        Original request with optional fencing or receipt fields.

    Returns
    -------
    tuple
        The original state mutation result.
    """
    ok, message = state.save_checkpoint(
        sender,
        task_id,
        checkpoint,
        epoch=optional_int_field(data, "epoch"),
    )
    return ok, message, state.claims.get(task_id) if ok else None

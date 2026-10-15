# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — atomic lease response and event preparation
"""Prepare lease responses and journal events without publishing live state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from synapse_channel.core.atomic_operations import OperationDraft
from synapse_channel.core.journal import EventKind
from synapse_channel.core.ledger import ProgressNote
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.receipts import (
    build_release_receipt,
    format_release_receipt_note,
    release_receipt_has_evidence,
)
from synapse_channel.core.release_confirmation import release_reply_binding

if TYPE_CHECKING:
    from synapse_channel.core.handlers.leasing import ClaimApplication, LeasingContext
    from synapse_channel.core.state_models import TaskClaim


def prepare_claim(
    application: ClaimApplication,
    *,
    hub: LeasingContext,
) -> OperationDraft | None:
    """Prepare the claim grant and its single durable event.

    Parameters
    ----------
    application
        Authoritative mutation outcome to represent.
    hub
        Transport and state context whose response factory is used.

    Returns
    -------
    OperationDraft or None
        Committed response and events, or None when the mutation was denied.
    """
    from synapse_channel.core.handlers.leasing import claim_grant_fields

    if application.claim is None:
        return None
    grant = hub.system(
        application.message,
        msg_type=MessageType.CLAIM_GRANTED,
        **claim_grant_fields(application.claim),
        renewal_id=application.renewal_id,
    )
    return OperationDraft(
        response=grant,
        events=((EventKind.CLAIM, application.claim.as_persisted_dict()),),
        intent={"family": "claim", "response_type": MessageType.CLAIM_GRANTED},
    )


def prepare_task_update(
    result: tuple[bool, str, TaskClaim | None],
    *,
    hub: LeasingContext,
    sender: str,
    task_id: str,
) -> OperationDraft | None:
    """Prepare the updated lease response and journal event.

    Parameters
    ----------
    result
        Authoritative mutation outcome to represent.
    hub
        Transport and state context whose response factory is used.
    sender
        Authenticated owner of this request.
    task_id
        Task identifier already normalised by the handler.

    Returns
    -------
    OperationDraft or None
        Committed response and events, or None when the mutation was denied.
    """
    ok, message, claim = result
    if not ok or claim is None:
        return None
    updated = hub.system(
        message,
        msg_type=MessageType.TASK_UPDATED,
        task_id=task_id,
        owner=sender,
        status=claim.status,
        data_ref=claim.data_ref,
        version=claim.version,
    )
    return OperationDraft(
        response=updated,
        events=((EventKind.TASK_UPDATE, claim.as_persisted_dict()),),
        intent={"family": "task_update", "response_type": MessageType.TASK_UPDATED},
    )


def prepare_release(
    result: tuple[bool, str],
    *,
    hub: LeasingContext,
    sender: str,
    task_id: str,
    data: dict[str, Any],
    prepared_progress: list[ProgressNote],
) -> OperationDraft | None:
    """Prepare the release grant and any assessment in one operation.

    Parameters
    ----------
    result
        Authoritative mutation outcome to represent.
    hub
        Transport and state context whose response factory is used.
    sender
        Authenticated owner of this request.
    task_id
        Task identifier already normalised by the handler.
    data
        Original request with optional fencing or receipt fields.
    prepared_progress
        Operation-owned progress slot restored only after commit.

    Returns
    -------
    OperationDraft or None
        Committed response and events, or None when the mutation was denied.
    """
    ok, message = result
    if not ok:
        return None
    receipt = build_release_receipt(
        task_id=task_id,
        owner=sender,
        evidence=data.get("evidence", ()),
        artifacts=data.get("artifacts", ()),
        known_failures=data.get("known_failures", ()),
        changed_files=data.get("changed_files", ()),
        generated_artifacts=data.get("generated_artifacts", ()),
        approvals=data.get("approvals", ()),
        confidence=data.get("confidence", ""),
        freshness_seconds=data.get("freshness_seconds"),
    )
    granted = hub.system(
        message,
        msg_type=MessageType.RELEASE_GRANTED,
        task_id=task_id,
        owner=sender,
        receipt=receipt,
        **release_reply_binding(data),
    )
    events: list[tuple[str, Mapping[str, Any]]] = [(EventKind.RELEASE, {"task_id": task_id})]
    if release_receipt_has_evidence(receipt):
        note = ProgressNote(
            task_id=task_id,
            author=sender,
            kind="assessment",
            text=format_release_receipt_note(receipt),
        )
        prepared_progress[:] = [note]
        events.append((EventKind.LEDGER_PROGRESS, note.as_dict()))
    return OperationDraft(
        response=granted,
        events=tuple(events),
        intent={"family": "release", "receipt": receipt},
    )


def prepare_handoff(
    result: tuple[bool, str, TaskClaim | None],
    *,
    hub: LeasingContext,
    sender: str,
    task_id: str,
    to_agent: str,
    prepared_progress: list[ProgressNote],
) -> OperationDraft | None:
    """Prepare the handoff grant and progress note in one operation.

    Parameters
    ----------
    result
        Authoritative mutation outcome to represent.
    hub
        Transport and state context whose response factory is used.
    sender
        Authenticated owner of this request.
    task_id
        Task identifier already normalised by the handler.
    to_agent
        Recipient already checked for online presence.
    prepared_progress
        Operation-owned progress slot restored only after commit.

    Returns
    -------
    OperationDraft or None
        Committed response and events, or None when the mutation was denied.
    """
    ok, message, claim = result
    if not ok or claim is None:
        return None
    scope_fields: dict[str, Any] = {"worktree": claim.worktree, "paths": list(claim.paths)}
    if claim.path_identity is not None:
        scope_fields["path_identity"] = claim.path_identity.as_dict()
    granted = hub.system(
        message,
        msg_type=MessageType.HANDOFF_GRANTED,
        task_id=task_id,
        owner=claim.owner,
        previous_owner=sender,
        note=claim.note,
        status=claim.status,
        **scope_fields,
        epoch=claim.epoch,
        version=claim.version,
        lease_expires_at=claim.lease_expires_at,
        checkpoint=claim.checkpoint,
    )
    progress = ProgressNote(
        task_id=task_id,
        author=sender,
        kind="note",
        text=(
            f"handed off to {to_agent}: {claim.note}" if claim.note else f"handed off to {to_agent}"
        ),
    )
    prepared_progress[:] = [progress]
    return OperationDraft(
        response=granted,
        events=(
            (EventKind.HANDOFF, claim.as_persisted_dict()),
            (EventKind.LEDGER_PROGRESS, progress.as_dict()),
        ),
        intent={
            "family": "handoff",
            "progress": {
                "task_id": task_id,
                "from_agent": sender,
                "to_agent": to_agent,
                "context": claim.note,
            },
        },
    )


def prepare_checkpoint(
    result: tuple[bool, str, TaskClaim | None],
    *,
    hub: LeasingContext,
    sender: str,
    task_id: str,
) -> OperationDraft | None:
    """Prepare a private checkpoint acknowledgement and durable event.

    Parameters
    ----------
    result
        Authoritative mutation outcome to represent.
    hub
        Transport and state context whose response factory is used.
    sender
        Authenticated owner of this request.
    task_id
        Task identifier already normalised by the handler.

    Returns
    -------
    OperationDraft or None
        Committed response and events, or None when the mutation was denied.
    """
    ok, message, claim = result
    if not ok or claim is None:
        return None
    saved = hub.system(
        message,
        msg_type=MessageType.CHECKPOINT_SAVED,
        target=sender,
        task_id=task_id,
        version=claim.version,
    )
    return OperationDraft(
        response=saved,
        events=((EventKind.CHECKPOINT, claim.as_persisted_dict()),),
        intent={"family": "checkpoint", "response_type": MessageType.CHECKPOINT_SAVED},
    )

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — lease-coordination handlers (claim/release/handoff/wait)
"""Lease-coordination handlers — the channel's mutual-exclusion core.

Each function applies one authoritative mutation to the hub's
:class:`~synapse_channel.core.state.SynapseState`: a scoped claim, its release, a
handoff to a present agent, a durable resume checkpoint, an owner's status
update, or an advisory wait. On success the change is journalled (when a durable
log is attached) and broadcast as a grant; on failure the sender is privately
denied. The hub is passed in so a handler reaches the shared state, journal, and
transport without the routing core knowing any verb's specifics. The git context
on a claim stays opaque here exactly as it is on the hub: stored and echoed,
never acted upon.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synapse_channel.core.deadlock import prune_waits, would_create_cycle
from synapse_channel.core.journal import (
    record_checkpoint,
    record_claim,
    record_handoff,
    record_ledger_progress,
    record_release,
    record_task_update,
)
from synapse_channel.core.numeric_coercion import safe_float
from synapse_channel.core.path_identity import (
    PathIdentityError,
    parse_optional_claim_scope_identity,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.receipts import (
    ReleaseReceipt,
    build_release_receipt,
    format_release_receipt_note,
    release_receipt_has_evidence,
)
from synapse_channel.core.state import GitContext
from synapse_channel.core.state_transaction import durable_state_transaction

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub
    from synapse_channel.core.state_models import TaskClaim


@dataclass(frozen=True)
class ClaimApplication:
    """The outcome of applying a claim to the hub's authoritative lease state.

    Attributes
    ----------
    ok : bool
        Whether the lease was acquired or renewed.
    message : str
        The human-readable grant or denial message from the state mutation.
    task_id : str
        The claimed task id as parsed from the request body (stripped); always present so
        a denial can still address the right task.
    claim : TaskClaim or None
        The granted lease on success, or ``None`` when the claim was refused.
    """

    ok: bool
    message: str
    task_id: str
    claim: TaskClaim | None


def apply_claim(
    hub: SynapseHub,
    claimant: str,
    body: Mapping[str, Any],
    *,
    quota_principal: str | None = None,
) -> ClaimApplication:
    """Apply a scoped claim to the hub's state on a claimant's behalf, journalling a grant.

    This is the authoritative grant core, shared by a direct claim and a claim forwarded
    from another hub. It reads the claim parameters from ``body`` exactly as a direct
    request does, applies the lease through
    :meth:`~synapse_channel.core.state.SynapseState.claim`, and on success clears the
    claimant's wait and journals the claim. It deliberately does **not** broadcast or relay
    the outcome: the caller decides whether a grant is announced locally
    (:func:`handle_claim`) or returned to a forwarding peer
    (:func:`synapse_channel.core.handlers.multihub_claim.handle_multihub_claim_request`), so
    the one place a claim is granted stays the one place its lease is mutated.

    Parameters
    ----------
    hub : SynapseHub
        The hub whose state and journal the claim is applied to.
    claimant : str
        The agent the lease is granted under — the direct sender, or the original claimant
        on whose behalf another hub forwards the request.
    body : dict[str, Any]
        The claim body: ``task_id`` (or ``payload``), and the optional ``note``,
        ``ttl_seconds``, ``worktree``, ``paths``, additive ``path_identity``, and
        ``git`` scope.
    quota_principal : str or None, optional
        Stable server-derived bucket charged for the claim. ``None`` preserves
        compatibility for direct internal callers by falling back to ``claimant``.

    Returns
    -------
    ClaimApplication
        The outcome; :attr:`ClaimApplication.claim` is set only when the lease was granted.
    """
    task_id = str(body.get("task_id") or body.get("payload") or "").strip()
    note = str(body.get("note") or "")
    ttl_seconds = body.get("ttl_seconds")
    worktree = str(body.get("worktree") or "")
    raw_paths = body.get("paths")
    paths = [str(p) for p in raw_paths] if isinstance(raw_paths, list) else []
    try:
        path_identity = parse_optional_claim_scope_identity(body)
    except PathIdentityError:
        return ClaimApplication(
            ok=False,
            message=f"Task '{task_id}' carries an invalid path identity.",
            task_id=task_id,
            claim=None,
        )
    # The git context is opaque to the hub: deserialise it for storage and
    # display, but never act on it (the hub runs no git, reads no filesystem).
    raw_git = body.get("git")
    git = GitContext.from_dict(raw_git) if isinstance(raw_git, dict) else None

    # A non-numeric, non-finite, boolean, or double-overflowing ttl falls back to
    # the hub's default lease duration (``None``) rather than raising out of the
    # frame handler or planting an ``inf``/``nan`` lease expiry.
    ttl_val = safe_float(ttl_seconds, default=None, allow_bool=False)

    with durable_state_transaction(hub.state, task_id, enabled=hub.journal is not None):
        ok, message = hub.state.claim(
            claimant,
            task_id,
            note=note,
            ttl_seconds=ttl_val,
            quota_principal=quota_principal,
            worktree=worktree,
            paths=paths,
            path_identity=path_identity,
            git=git,
        )
        claim = hub.state.claims.get(task_id) if ok else None
        if claim is not None and hub.journal is not None:
            record_claim(hub.journal, claim)
    if claim is not None:
        # Only the SATISFIED wait is cleared: a claim or renewal for task U must
        # never erase the claimant's still-open wait on an unrelated task T.
        waited = hub._waits.get(claimant)
        if waited is not None:
            waited.discard(task_id)
            if not waited:
                hub._waits.pop(claimant, None)
        return ClaimApplication(ok=True, message=message, task_id=task_id, claim=claim)
    return ClaimApplication(ok=False, message=message, task_id=task_id, claim=None)


def claim_grant_fields(claim: TaskClaim) -> dict[str, Any]:
    """Return the ``CLAIM_GRANTED`` message fields for a granted claim.

    Shared by every path that announces a grant — a direct claim broadcast and a forwarded
    claim relayed back to its originating hub — so the grant a client sees is identical
    however the claim was routed.

    Parameters
    ----------
    claim : TaskClaim
        The granted lease.

    Returns
    -------
    dict[str, Any]
        The grant fields: task id, owner, note, lease expiry, status, worktree, paths,
        additive path identity, epoch, version, checkpoint, and the opaque git
        context (``None`` when unset).
    """
    fields: dict[str, Any] = {
        "task_id": claim.task_id,
        "owner": claim.owner,
        "note": claim.note,
        "lease_expires_at": claim.lease_expires_at,
        "status": claim.status,
        "worktree": claim.worktree,
        "paths": list(claim.paths),
        "epoch": claim.epoch,
        "version": claim.version,
        "checkpoint": claim.checkpoint,
        "git": claim.git.as_dict() if claim.git is not None else None,
    }
    if claim.path_identity is not None:
        fields["path_identity"] = claim.path_identity.as_dict()
    return fields


async def handle_claim(hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any) -> None:
    """Apply a scoped claim request and broadcast the grant, or deny the sender."""
    application = apply_claim(
        hub,
        sender,
        data,
        quota_principal=hub.clients.quota_principal(websocket, fallback_agent=sender),
    )
    if application.claim is not None:
        hub.counters.claims_granted += 1
        grant = hub._system(
            application.message,
            msg_type=MessageType.CLAIM_GRANTED,
            **claim_grant_fields(application.claim),
        )
        hub._remember(data, grant)
        await hub._broadcast(grant)
        return
    hub.counters.claims_denied += 1
    await hub._send_json(
        websocket,
        hub._system(
            application.message,
            msg_type=MessageType.CLAIM_DENIED,
            target=sender,
            task_id=application.task_id,
        ),
    )


async def handle_task_update(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Apply an owner's status/note/data-ref update and broadcast it."""
    task_id = str(data.get("task_id") or data.get("id") or "").strip()
    status = data.get("status")
    note = data.get("note")
    data_ref = data.get("data_ref")

    with durable_state_transaction(hub.state, task_id, enabled=hub.journal is not None):
        ok, message = hub.state.update_task(
            sender,
            task_id,
            status=str(status) if status else None,
            note=str(note) if note is not None else None,
            data_ref=str(data_ref) if data_ref is not None else None,
            epoch=hub._optional_int(data, "epoch"),
            expected_version=hub._optional_int(data, "expected_version"),
        )
        claim = hub.state.claims.get(task_id) if ok else None
        if claim is not None and hub.journal is not None:
            record_task_update(hub.journal, claim)
    if claim is not None:
        updated = hub._system(
            message,
            msg_type=MessageType.TASK_UPDATED,
            task_id=task_id,
            owner=sender if claim else None,
            status=claim.status if claim else None,
            data_ref=claim.data_ref if claim else None,
            version=claim.version if claim else None,
        )
        hub._remember(data, updated)
        await hub._broadcast(updated)
    else:
        await hub._send_json(
            websocket, hub._system(message, msg_type=MessageType.ERROR, target=sender)
        )


async def handle_release(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Release a task and broadcast it, or deny the sender."""
    task_id = str(data.get("task_id") or data.get("payload") or "").strip()
    with durable_state_transaction(hub.state, task_id, enabled=hub.journal is not None):
        ok, message = hub.state.release(sender, task_id, epoch=hub._optional_int(data, "epoch"))
        if ok and hub.journal is not None:
            record_release(hub.journal, task_id)
    if ok:
        # A released task has no holder: prune its wait edges so they cannot
        # refuse a later legitimate wait as a false-positive deadlock.
        hub._waits = prune_waits(hub._waits, hub.state.claims)
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
        hub.counters.releases_granted += 1
        granted = hub._system(
            message,
            msg_type=MessageType.RELEASE_GRANTED,
            task_id=task_id,
            owner=sender,
            receipt=receipt,
        )
        hub._remember(data, granted)
        await hub._broadcast(granted)
        if release_receipt_has_evidence(receipt):
            await _record_release_receipt_progress(hub, receipt)
        return
    await hub._send_json(
        websocket,
        hub._system(
            message,
            msg_type=MessageType.RELEASE_DENIED,
            target=sender,
            task_id=task_id,
        ),
    )


async def _record_release_receipt_progress(hub: SynapseHub, receipt: ReleaseReceipt) -> None:
    """Record a release receipt as a blackboard assessment note."""
    ok, result = hub.blackboard.post_progress(
        task_id=str(receipt["task_id"]),
        author=str(receipt["owner"]),
        kind="assessment",
        text=format_release_receipt_note(receipt),
    )
    if not ok or isinstance(result, str):
        return
    note = result
    if hub.journal is not None:
        record_ledger_progress(hub.journal, note)
    await hub._broadcast(
        hub._system(
            f"Release receipt from {receipt['owner']}",
            msg_type=MessageType.LEDGER_PROGRESS_POSTED,
            note=note.as_dict(),
        )
    )


async def handle_handoff(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Transfer an owned task to an online agent and broadcast it, or deny.

    The recipient must be currently online so the work actually moves to a
    present agent. On success the move is also recorded as a progress note on
    the shared blackboard, so the supervisor sees who handed what to whom.
    """
    task_id = str(data.get("task_id") or "").strip()
    to_agent = str(data.get("to_agent") or data.get("target") or "").strip()
    note = data.get("note")

    if to_agent and to_agent not in hub.agent_sockets:
        await hub._send_json(
            websocket,
            hub._system(
                f"Handoff target '{to_agent}' is not online.",
                msg_type=MessageType.HANDOFF_DENIED,
                target=sender,
                task_id=task_id,
            ),
        )
        return

    with durable_state_transaction(
        hub.state,
        task_id,
        enabled=hub.journal is not None,
        restore_presence=(to_agent,),
    ):
        ok, message = hub.state.handoff(
            sender,
            task_id,
            to_agent,
            note=str(note) if note is not None else None,
            epoch=hub._optional_int(data, "epoch"),
        )
        claim = hub.state.claims.get(task_id) if ok else None
        if claim is not None and hub.journal is not None:
            record_handoff(hub.journal, claim)
    if claim is None:
        await hub._send_json(
            websocket,
            hub._system(
                message,
                msg_type=MessageType.HANDOFF_DENIED,
                target=sender,
                task_id=task_id,
            ),
        )
        return

    # Receiving task X clears only the recipient's wait on X, never a wait on
    # an unrelated task the recipient is still blocked on.
    waited = hub._waits.get(to_agent)
    if waited is not None:
        waited.discard(task_id)
        if not waited:
            hub._waits.pop(to_agent, None)
    await _record_handoff_progress(hub, task_id, sender, to_agent, claim.note)
    scope_fields: dict[str, Any] = {
        "worktree": claim.worktree,
        "paths": list(claim.paths),
    }
    if claim.path_identity is not None:
        scope_fields["path_identity"] = claim.path_identity.as_dict()
    granted = hub._system(
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
    hub._remember(data, granted)
    await hub._broadcast(granted)


async def _record_handoff_progress(
    hub: SynapseHub, task_id: str, from_agent: str, to_agent: str, context: str
) -> None:
    """Log a handoff as a progress note and broadcast it to observers."""
    text = f"handed off to {to_agent}: {context}" if context else f"handed off to {to_agent}"
    note = hub.blackboard.note(task_id=task_id, author=from_agent, text=text)
    if hub.journal is not None:
        record_ledger_progress(hub.journal, note)
    await hub._broadcast(
        hub._system(
            f"Progress from {from_agent}",
            msg_type=MessageType.LEDGER_PROGRESS_POSTED,
            note=note.as_dict(),
        )
    )


async def handle_checkpoint(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Save a resume checkpoint on an owned task, acking the owner, or deny.

    The checkpoint is durable and survives lease expiry, so a later claimant
    of the same task resumes from it. The ack is private to the owner.
    """
    task_id = str(data.get("task_id") or "").strip()
    checkpoint = str(data.get("checkpoint") or data.get("payload") or "")
    with durable_state_transaction(hub.state, task_id, enabled=hub.journal is not None):
        ok, message = hub.state.save_checkpoint(
            sender,
            task_id,
            checkpoint,
            epoch=hub._optional_int(data, "epoch"),
        )
        claim = hub.state.claims.get(task_id) if ok else None
        if claim is not None and hub.journal is not None:
            record_checkpoint(hub.journal, claim)
    if claim is not None:
        saved = hub._system(
            message,
            msg_type=MessageType.CHECKPOINT_SAVED,
            target=sender,
            task_id=task_id,
            version=claim.version,
        )
        hub._remember(data, saved)
        await hub._send_json(websocket, saved)
        return
    await hub._send_json(
        websocket,
        hub._system(
            message,
            msg_type=MessageType.CHECKPOINT_DENIED,
            target=sender,
            task_id=task_id,
        ),
    )


async def handle_wait_request(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Register an advisory wait for a held task, refusing deadlock.

    The wait is advisory: the hub records that ``sender`` waits for whoever
    holds ``task_id`` and refuses the request if registering it would close a
    wait-for cycle (a hold-and-wait deadlock). The waiter is expected to retry
    its claim when the holder releases; the wait clears on its next successful
    claim or on disconnect.

    Parameters
    ----------
    hub : SynapseHub
        The hub whose wait graph and transport the handler uses.
    sender : str
        The agent requesting to wait.
    data : dict[str, Any]
        The request; ``task_id`` is the task to wait for.
    websocket : Any
        The requesting socket.
    """
    task_id = str(data.get("task_id") or data.get("payload") or "").strip()
    claim = hub.state.claims.get(task_id)
    if claim is None:
        await hub._send_json(
            websocket,
            hub._system(
                f"Task '{task_id}' is not claimed; nothing to wait for.",
                msg_type=MessageType.WAIT_DENIED,
                target=sender,
                task_id=task_id,
            ),
        )
        return
    holder = claim.owner
    if holder == sender:
        await hub._send_json(
            websocket,
            hub._system(
                f"You already hold '{task_id}'.",
                msg_type=MessageType.WAIT_DENIED,
                target=sender,
                task_id=task_id,
            ),
        )
        return
    if would_create_cycle(hub._waits, hub.state.claims, sender, holder):
        await hub._send_json(
            websocket,
            hub._system(
                f"Waiting for '{task_id}' held by {holder} would deadlock.",
                msg_type=MessageType.WAIT_DENIED,
                target=sender,
                task_id=task_id,
                holder=holder,
            ),
        )
        return
    # The edge keys the waited TASK, not the incumbent holder: ownership is
    # resolved live at cycle-check time, so a later handoff or release can
    # never leave a stale agent edge behind.
    hub._waits.setdefault(sender, set()).add(task_id)
    await hub._send_json(
        websocket,
        hub._system(
            f"Waiting for '{task_id}' held by {holder}.",
            msg_type=MessageType.WAIT_GRANTED,
            target=sender,
            task_id=task_id,
            holder=holder,
        ),
    )

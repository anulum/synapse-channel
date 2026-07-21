# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — map hub frames to ACL accesses and authorise them
"""Map a hub frame to the ACL accesses it requires and authorise it.

This is the runtime-enforcement layer over the deny-by-default ACL model: it
turns a mutating frame into the structured ``(permission, target)`` accesses it
needs, then evaluates them so the hub can reject an unauthorised frame before it
mutates state. The same evaluator backs shadow mode, so a frame's enforced
decision matches what ``synapse acl shadow`` reported.

Authentication (who the sender is) is the per-message-authentication and connect
layers; authorisation (whether that sender may perform the verb on the target) is
here. Namespace-scoped rules are therefore only as strong as the sender binding:
on an unauthenticated hub, or for a gated verb that per-message authentication
does not sign, the ``sender`` is self-reported, so enforcement must be paired
with a connect token and per-message auth before it is a real boundary.

Every mutating agent->hub verb is gated (see :data:`GATED_MUTATIONS`); read and
query surfaces (metrics, dashboard, event-query) are a later tranche, and a
read/query frame is allowed so a shared-token local hub keeps working with
enforcement off.

See :doc:`../../docs/identity-and-acl` for the design.
"""

from __future__ import annotations

from typing import Any

from synapse_channel.core.acl import (
    BOARD,
    CLAIM,
    EVIDENCE,
    MESSAGE,
    PIN_RECLAIM,
    RECALL,
    RELEASE,
    AclDecision,
    AclPolicy,
    Target,
    evaluate_access,
)
from synapse_channel.core.protocol import RESOURCE_TYPE_ALIASES, MessageType
from synapse_channel.core.scoping import MAX_DECLARED_PATHS, normalize_paths

_BOARD_TYPES = frozenset(
    {
        MessageType.LEDGER_TASK,
        MessageType.LEDGER_TASK_UPDATE,
        MessageType.LEDGER_PROGRESS,
        MessageType.FINDING,
    }
)
_CHANNEL_TYPES = frozenset(
    {MessageType.CHANNEL_CREATE, MessageType.CHANNEL_JOIN, MessageType.CHANNEL_LEAVE}
)
_RECALL_TYPES = frozenset({MessageType.HISTORY_REQUEST, MessageType.RESUME_REQUEST})
"""Read verbs that pull the hub's global chat history / resume backlog.

Unlike :data:`GATED_MUTATIONS` these do not mutate state, but they are ACL-gated
reads: under ``--require-acl`` a deny-by-default policy governs them through the
``RECALL`` permission, so a secured hub no longer serves its full history to any
authenticated agent. With enforcement off they stay ungated like every other read."""
_TASK_PAYLOAD_FALLBACK = frozenset(
    {MessageType.CLAIM, MessageType.TASK_UPDATE, MessageType.HANDOFF}
)

GATED_MUTATIONS = (
    frozenset(
        {
            MessageType.CHAT,
            MessageType.CLAIM,
            MessageType.TASK_UPDATE,
            MessageType.HANDOFF,
            MessageType.CHECKPOINT,
            MessageType.RELEASE,
            MessageType.ADVERTISE,
            MessageType.IDENTITY_PIN_RECLAIM,
            MessageType.GUARD_DENIAL,
        }
    )
    | _BOARD_TYPES
    | _CHANNEL_TYPES
    | RESOURCE_TYPE_ALIASES
)
"""Every agent->hub frame type that mutates or broadcasts state and is ACL-gated.

A frame outside this set is a read/query/keepalive and passes, with the sole
exception of the :data:`_RECALL_TYPES` history/resume reads, which are ACL-gated
too (they expose the full chat backlog). A future mutating type MUST be added here
and mapped in :func:`required_accesses`, which the
``test_every_gated_mutation_is_mapped`` test enforces so a new mutation cannot be
silently ungated."""


def project_of(subject: str) -> str:
    """Return the project namespace prefix of an identity, or ``""``.

    ``SYNAPSE-CHANNEL/claude-e57b`` resolves to ``SYNAPSE-CHANNEL``; a bare name
    with no ``/`` has no namespace.
    """
    name = str(subject or "")
    return name.split("/", 1)[0] if "/" in name else ""


def _string_list(data: dict[str, Any], key: str) -> list[str]:
    """Return a frame field as a list of non-empty strings."""
    value = data.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _task_id(msg_type: str, data: dict[str, Any]) -> str:
    """Resolve a frame's task id exactly as the claim/release handlers do."""
    if msg_type in _TASK_PAYLOAD_FALLBACK:
        return str(data.get("task_id") or data.get("payload") or "").strip()
    return str(data.get("task_id") or "").strip()


def required_accesses(msg_type: str, data: dict[str, Any]) -> list[tuple[str, Target]]:
    """Return the ACL accesses a frame requires; an empty list means ungated.

    The accesses are derived from the values the handler actually acts on, not the
    raw frame, so the gate cannot be bypassed by a mapper/handler divergence: the
    task id uses the same ``task_id``-or-``payload`` fallback, and paths are
    normalised with the same :func:`normalize_paths` the claim handler applies
    (so ``src/..`` widening to the worktree root is checked as the root scope,
    not as the literal ``src/..``). A claim needs its task-id access *and* each
    normalised path access; the authoriser requires all of them.

    Parameters
    ----------
    msg_type : str
        The inbound message type.
    data : dict[str, Any]
        The frame payload.

    Returns
    -------
    list[tuple[str, Target]]
        One ``(permission, target)`` per access the frame needs.
    """
    if msg_type == MessageType.CHAT:
        channel = str(data.get("channel") or "").strip()
        if channel:
            return [(MESSAGE, Target("channel", channel))]
        return [(MESSAGE, Target("agent", str(data.get("target") or "all")))]
    if msg_type in (MessageType.CLAIM, MessageType.TASK_UPDATE, MessageType.HANDOFF):
        accesses = [(CLAIM, Target("claim", _task_id(msg_type, data)))]
        for path in normalize_paths(_string_list(data, "paths"), MAX_DECLARED_PATHS):
            accesses.append((CLAIM, Target("path", path)))
        return accesses
    if msg_type == MessageType.CHECKPOINT:
        return [(CLAIM, Target("claim", _task_id(msg_type, data)))]
    if msg_type == MessageType.RELEASE:
        return [(RELEASE, Target("claim", _task_id(msg_type, data)))]
    if msg_type in _BOARD_TYPES:
        return [(BOARD, Target("board", str(data.get("task_id") or "*")))]
    if msg_type in RESOURCE_TYPE_ALIASES:
        return [(BOARD, Target("resource", str(data.get("name") or "*")))]
    if msg_type == MessageType.ADVERTISE:
        return [(BOARD, Target("capability", str(data.get("agent") or "*")))]
    if msg_type == MessageType.IDENTITY_PIN_RECLAIM:
        return [(PIN_RECLAIM, Target("agent", str(data.get("pin_name") or "")))]
    if msg_type == MessageType.GUARD_DENIAL:
        return [(EVIDENCE, Target("evidence", "guard-denial"))]
    if msg_type in _CHANNEL_TYPES:
        return [(MESSAGE, Target("channel", str(data.get("channel") or "")))]
    if msg_type in _RECALL_TYPES:
        return [(RECALL, Target("history", "global"))]
    return []


def authorise_frame(
    *, sender: str, msg_type: str, data: dict[str, Any], policy: AclPolicy
) -> AclDecision | None:
    """Return the first deny decision for a frame, or ``None`` when authorised.

    An ungated read/query frame (no required accesses) returns ``None``. Every
    required access must be allowed; the first ``would_deny`` is returned so the
    hub can reject the frame and record the reason. A frame that is a known
    mutation but produces no accesses fails closed — it is denied rather than
    silently allowed — so a future unmapped mutating verb cannot slip the gate.
    """
    project = project_of(sender)
    accesses = required_accesses(msg_type, data)
    if not accesses:
        if msg_type in GATED_MUTATIONS:
            return AclDecision(
                "would_deny",
                sender,
                msg_type,
                Target("frame", msg_type),
                "mutating frame has no ACL mapping (deny by default)",
            )
        return None
    for permission, target in accesses:
        decision = evaluate_access(
            subject=sender,
            project=project,
            permission=permission,
            target=target,
            policy=policy,
        )
        if decision.decision != "would_allow":
            return decision
    return None

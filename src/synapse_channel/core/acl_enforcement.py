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

from synapse_channel.core.acl import AclDecision, AclPolicy, Target, evaluate_access
from synapse_channel.core.handlers import VERBS
from synapse_channel.core.identity_namespace import project_of as project_of

GATED_MUTATIONS = frozenset(request for request, spec in VERBS.items() if spec.mutation_guarded)
"""Legacy ACL/journal guard types, including attachment reads but not history reads.

The dispositions belong to handler specs. A guarded declaration without an ACL
mapper is refused while collecting the registry, before any hub can route it.
"""


def required_accesses(msg_type: str, data: dict[str, Any]) -> list[tuple[str, Target]]:
    """Return the accesses declared by the matching handler; unknown types are ungated.

    A claim's task/payload fallback and normalized paths match its handler.
    Task updates resolve task_id/id, and releases resolve task_id/payload, so
    an alternate field cannot authorize a different task from the one mutated.
    History reads distinguish mailbox queries from global recall. Each family
    selects its own mapper; enforcement never maintains another wire-type ladder.

    Parameters
    ----------
    msg_type : str
        Normalized inbound message type, with existing unknown-type behavior.
    data : dict[str, Any]
        The frame fields consumed by the handler.

    Returns
    -------
    list[tuple[str, Target]]
        Every required access, or an empty list for an unmapped request.
    """
    spec = VERBS.get(msg_type)
    return spec.accesses(data) if spec is not None and spec.accesses is not None else []


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

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one serving-side authorisation for a mutation a federated peer drives
"""One serving-side authorisation for every mutation a federated peer drives on this hub.

An owning hub serves three peer-driven mutations: a relayed operator action, a forwarded
claim, and a dead-letter pointer. Each names a namespace. :func:`authorise_federated_mutation`
decides, for that namespace, the three things every one of them needs:

* the peer is trusted **for the request's namespace**
  (:meth:`~synapse_channel.core.multihub_serving.MultiHubServingPolicy.authorise_namespace`),
  never merely for the namespace of its own grant;
* its peering scope grants the action's verb in that namespace, when the action has a verb;
* this hub owns the namespace uncontested, resolved with the hub ids observed asserting it, so
  a namespace another hub is seen to claim is not served.

The caller then binds the object it touches to the same namespace, for example with
:func:`claim_namespace`: authorising who calls is not enough when the request can name a task
in another namespace.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from synapse_channel.core.federation import ScopeGrant
    from synapse_channel.core.multihub_serving import MultiHubServingPolicy
    from synapse_channel.core.namespace_ownership import NamespaceOwnership


@dataclass(frozen=True)
class FederatedServing:
    """The serving-side decision for one peer-driven mutation in one namespace.

    Attributes
    ----------
    peer_authorised : bool
        Whether the peer is trusted for the namespace through every trust layer.
    principal : str
        The peer's verified federation principal; empty when it is not authorised.
    scope : tuple of ScopeGrant
        The bounded scope the peering maps for the namespace; empty on a deny.
    scope_granted : bool
        Whether that scope grants the requested verb in the namespace. ``True`` when the
        caller asked for no verb.
    namespace_owned : bool
        Whether this hub owns the namespace, uncontested by any observed asserting hub.
    """

    peer_authorised: bool
    principal: str
    scope: tuple[ScopeGrant, ...]
    scope_granted: bool
    namespace_owned: bool


def authorise_federated_mutation(
    *,
    policy: MultiHubServingPolicy | None,
    ownership: NamespaceOwnership | None,
    observed_asserting_hubs: Callable[[str], Iterable[str]] | None,
    sender: str,
    websocket: Any,
    namespace: str,
    verb: str | None,
) -> FederatedServing:
    """Authorise a peer-driven mutation in ``namespace`` on the serving side.

    Parameters
    ----------
    policy : MultiHubServingPolicy or None
        The hub's serving policy; ``None`` authorises no peer.
    ownership : NamespaceOwnership or None
        The hub's ownership map; ``None`` owns nothing.
    observed_asserting_hubs : Callable or None
        The feed of hub ids observed asserting authority over a namespace.
    sender : str
        The peer hub's registered id.
    websocket : Any
        The peer's connection, read by the policy for its live certificate.
    namespace : str
        The namespace the request acts in.
    verb : str or None
        The scope verb the action needs. It has no default, so every caller states it.
        ``None`` is reserved for a dead-letter pointer: it changes no coordination state,
        it only journals an audit event and tells operators, so it needs no scope verb;
        it is still authorised for the namespace and checked for ownership. A structural
        test pins the callers that may pass ``None``.

    Returns
    -------
    FederatedServing
        Every check's outcome, for the caller to refuse in its own order.
    """
    authorisation = (
        policy.authorise_namespace(sender=sender, websocket=websocket, namespace=namespace)
        if policy is not None and namespace
        else None
    )
    peer_authorised = (
        authorisation is not None and authorisation.allowed and bool(authorisation.principal)
    )
    scope = authorisation.scope if authorisation is not None and peer_authorised else ()
    scope_granted = verb is None or any(
        grant.verb == verb and grant.namespace == namespace for grant in scope
    )
    asserting = tuple(observed_asserting_hubs(namespace)) if observed_asserting_hubs else ()
    namespace_owned = (
        ownership is not None
        and bool(namespace)
        and ownership.resolve(namespace, asserting_hubs=asserting).grants_locally
    )
    return FederatedServing(
        peer_authorised=peer_authorised,
        principal=authorisation.principal if authorisation is not None and peer_authorised else "",
        scope=tuple(scope),
        scope_granted=scope_granted,
        namespace_owned=namespace_owned,
    )


def claim_namespace(owner: str) -> str | None:
    """Return the namespace a claim belongs to: its owner's project segment.

    Parameters
    ----------
    owner : str
        The claim's owner, ``<project>/<seat>``.

    Returns
    -------
    str or None
        The project segment, or ``None`` for an owner with no project segment, which no
        namespace-scoped federated action may touch.
    """
    project, separator, seat = owner.partition("/")
    if not separator or not project or not seat:
        return None
    return project

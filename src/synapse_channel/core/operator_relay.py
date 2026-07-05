# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deny-by-default policy for governed cross-hub operator relays
"""Deny-by-default policy for relaying a governed operator action to a peer hub.

An operator on one domain can ask a peer hub to perform a bounded governed action inside a
namespace that peer owns — the first being a **force-release** of a stuck lease. Because a
relay mutates a peer's state on an operator's authority, this module owns the *policy* half:
which actions may be relayed at all, and whether a given relay is authorised. It is the
operator-relay counterpart to :mod:`synapse_channel.core.federation` — pure, I/O-free, no
crypto — and it **composes** with the checks that already exist rather than replacing them.

Two deny-by-default rules define what a relay permits:

* **only registered actions relay.** :data:`RELAYABLE_ACTIONS` is an explicit allowlist; an
  action not in it is refused, so a new relayable capability is a deliberate registry entry,
  never an accident of the wire format.
* **an authorised relay is one every layer permits.** :func:`authorise_relay` allows a relay
  only when the peer is a verified federated hub (mutual TLS + federation, checked by
  :class:`~synapse_channel.core.multihub_serving.MultiHubServingPolicy`), the peering's bounded
  scope grants the action's verb in the target namespace, and — for a state-mutating action —
  this hub authoritatively owns that namespace. Any layer refusing refuses the relay.

The verb an action maps to is an :mod:`~synapse_channel.core.acl` permission, so a peering
grants "may relay a release into namespace N" with the same :class:`~synapse_channel.core.
federation.ScopeGrant` machinery it grants any bounded cross-domain access — no new grant
vocabulary. This module never performs the action; the serving handler applies it behind an
allow decision and audits it with explicit cross-hub provenance.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from synapse_channel.core import acl
from synapse_channel.core.federation import ScopeGrant
from synapse_channel.core.operator_relay_wire import RelayActionRequest

RELAY_RELEASE = "release"
"""Action id: force-release a lease the peer hub holds, on the operator's verified authority."""


@dataclass(frozen=True, slots=True)
class RelayableAction:
    """One action an operator may relay to a peer hub, and how it is authorised.

    Attributes
    ----------
    action_id : str
        The stable id named on the wire and resolved against :data:`RELAYABLE_ACTIONS`.
    verb : str
        The :mod:`~synapse_channel.core.acl` permission a peering's
        :class:`~synapse_channel.core.federation.ScopeGrant` must grant in the target
        namespace for the relay to be authorised — the same scope vocabulary the local ACL uses.
    requires_ownership : bool
        Whether the acting hub must authoritatively own the target namespace. A
        state-mutating action (a release) requires it; a hub never mutates a namespace another
        hub owns.
    description : str
        A short human-readable description of what the action does.
    """

    action_id: str
    verb: str
    requires_ownership: bool
    description: str


RELAYABLE_ACTIONS: dict[str, RelayableAction] = {
    RELAY_RELEASE: RelayableAction(
        action_id=RELAY_RELEASE,
        verb=acl.RELEASE,
        requires_ownership=True,
        description="force-release a lease held on the peer hub",
    ),
}
"""The deny-by-default allowlist of relayable actions, keyed by action id.

An action absent from this mapping is refused outright — a relayable capability is a
deliberate registry entry, so the wire format alone can never smuggle in a new action.
"""


class RelayDenyReason:
    """Reasons a relay authorisation is refused (deny-by-default)."""

    UNKNOWN_ACTION = "unknown_action"
    PEER_NOT_AUTHORISED = "peer_not_authorised"
    SCOPE_NOT_GRANTED = "scope_not_granted"
    NAMESPACE_NOT_OWNED = "namespace_not_owned"


RELAY_AUTHORISED = "authorised"
"""Reason string on an allowed relay decision."""


@dataclass(frozen=True, slots=True)
class RelayDecision:
    """The outcome of authorising one relayed operator action.

    Attributes
    ----------
    allowed : bool
        Whether the relay may be applied.
    action : str
        The action id the decision concerns.
    reason : str
        :data:`RELAY_AUTHORISED`, or a :class:`RelayDenyReason` value.
    """

    allowed: bool
    action: str
    reason: str


def authorise_relay(
    request: RelayActionRequest,
    *,
    peer_authorised: bool,
    scope: Iterable[ScopeGrant],
    owns_namespace: bool,
) -> RelayDecision:
    """Decide whether a relayed operator action may be applied, deny-by-default.

    The checks run fail-closed in order so an unverified peer learns only that it is not
    authorised, never whether the action or namespace is valid:

    1. the peer must be a verified federated hub (``peer_authorised``) — a relay from an
       unauthenticated or unpinned peer is refused before anything else;
    2. the action must be in :data:`RELAYABLE_ACTIONS`;
    3. the peering's bounded ``scope`` must grant the action's verb in the request's namespace;
    4. for an action that mutates state, this hub must authoritatively own the namespace.

    The first failure returns its :class:`RelayDenyReason`; otherwise the relay is authorised.
    This is only the relay-policy gate — the caller has already composed the mutual-TLS and
    federation checks into ``peer_authorised`` and ``scope`` via
    :class:`~synapse_channel.core.multihub_serving.MultiHubServingPolicy`, and resolved
    ``owns_namespace`` via :class:`~synapse_channel.core.namespace_ownership.NamespaceOwnership`.

    Parameters
    ----------
    request : RelayActionRequest
        The relayed action and the namespace and task it acts on.
    peer_authorised : bool
        Whether the peer cleared the hub's serving policy (mutual TLS + federation).
    scope : Iterable[ScopeGrant]
        The bounded scope the peering maps the remote operator to.
    owns_namespace : bool
        Whether this hub authoritatively and uncontestedly owns the target namespace.

    Returns
    -------
    RelayDecision
        Allowed with :data:`RELAY_AUTHORISED`, or denied with the first failing reason.
    """
    if not peer_authorised:
        return RelayDecision(False, request.action, RelayDenyReason.PEER_NOT_AUTHORISED)
    action = RELAYABLE_ACTIONS.get(request.action)
    if action is None:
        return RelayDecision(False, request.action, RelayDenyReason.UNKNOWN_ACTION)
    if not _scope_grants(scope, verb=action.verb, namespace=request.namespace):
        return RelayDecision(False, request.action, RelayDenyReason.SCOPE_NOT_GRANTED)
    if action.requires_ownership and not owns_namespace:
        return RelayDecision(False, request.action, RelayDenyReason.NAMESPACE_NOT_OWNED)
    return RelayDecision(True, request.action, RELAY_AUTHORISED)


def _scope_grants(scope: Iterable[ScopeGrant], *, verb: str, namespace: str) -> bool:
    """Return whether ``scope`` grants ``verb`` in ``namespace`` (deny-closed on an empty scope).

    Mirrors :func:`~synapse_channel.core.federation.scope_authorises` for a single access: a
    remote operator inherits no local default, so a scope granting nothing authorises nothing.
    """
    return any(grant.verb == verb and grant.namespace == namespace for grant in scope)

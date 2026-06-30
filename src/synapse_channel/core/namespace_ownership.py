# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — namespace ownership: single-authoritative-hub resolution for claim routing
"""Namespace ownership — the single-authoritative-hub resolution behind claim routing.

A claim is mutual exclusion, not a mergeable value: two hubs independently granting the same
file scope is precisely the collision the claim prevents (`docs/multi-hub-sync.md`). So claims
are not synced as a CRDT; they are *routed by namespace ownership*. Each project namespace has
exactly one authoritative owning hub at a time, and only that hub grants claims inside it —
there is never a conflicting grant to merge.

This module is the pure decision behind that rule. A :class:`NamespaceOwnership` map records,
per namespace, the operator-confirmed owning hub id, and :meth:`NamespaceOwnership.resolve`
turns a namespace into one of four outcomes for the hub holding the map: it owns the namespace
and grants locally, a named peer owns it and the request must be routed there, no operator has
governed the namespace, or ownership is contested. The last two refuse, fail-closed: claim
safety never grants on doubt. Partition — two hubs both believing they own a namespace — is
detected by passing the owners observed asserting authority at runtime; any asserting hub other
than the configured owner contests ownership and refuses every grant until it is re-established.

The module owns no network and no claim state: it decides *who may grant*, leaving the local
grant path and the cross-hub forwarding to their own layers, exactly as the federation policy
decides *what a peering permits* without owning the mTLS or ACL checks it composes with.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum


class OwnershipOutcome(str, Enum):
    """The four outcomes of resolving which hub may grant claims in a namespace."""

    LOCAL = "local"
    """The hub holding the map owns the namespace and grants claims locally."""

    REMOTE = "remote"
    """A named peer hub owns the namespace; a real claim must be routed to it."""

    UNGOVERNED = "ungoverned"
    """No operator has assigned an owner; the namespace fails closed (no grant)."""

    PARTITIONED = "partitioned"
    """Ownership is contested by more than one hub; every grant fails closed."""


_REFUSING = frozenset({OwnershipOutcome.UNGOVERNED, OwnershipOutcome.PARTITIONED})


@dataclass(frozen=True)
class OwnershipDecision:
    """The resolution of a namespace to the hub authorised to grant claims in it.

    Attributes
    ----------
    outcome : OwnershipOutcome
        Which of the four cases the namespace resolves to for the resolving hub.
    namespace : str
        The namespace that was resolved.
    owner_hub_id : str or None
        The authoritative owner's hub id when one is established — the resolving hub for
        :data:`~OwnershipOutcome.LOCAL`, the named peer for :data:`~OwnershipOutcome.REMOTE`.
        ``None`` when ungoverned or partitioned, where no single owner holds.
    contesting : tuple[str, ...]
        On :data:`~OwnershipOutcome.PARTITIONED`, the hub ids contesting ownership beyond the
        configured owner, sorted; empty otherwise.
    """

    outcome: OwnershipOutcome
    namespace: str
    owner_hub_id: str | None = None
    contesting: tuple[str, ...] = field(default_factory=tuple)

    @property
    def grants_locally(self) -> bool:
        """Return whether the resolving hub may grant claims in the namespace itself."""
        return self.outcome is OwnershipOutcome.LOCAL

    @property
    def refuses(self) -> bool:
        """Return whether the namespace fails closed (ungoverned or partitioned)."""
        return self.outcome in _REFUSING


@dataclass(frozen=True)
class NamespaceOwnership:
    """An operator-confirmed map of namespace to its single authoritative owning hub.

    Deny-by-default: a namespace absent from the map is ungoverned and grants nothing. The map
    is the resolving hub's own static belief; partition is detected against the owners observed
    asserting authority at resolution time, so a stale or split configuration cannot silently
    grant a contested namespace.

    Attributes
    ----------
    owners : Mapping[str, str]
        Namespace to owning hub id. Exactly one owner per namespace.
    local_hub_id : str
        The hub id of the hub holding this map, compared against each namespace's owner to
        decide local versus remote authority.
    """

    owners: Mapping[str, str]
    local_hub_id: str

    def owner_of(self, namespace: str) -> str | None:
        """Return the configured owning hub id for ``namespace``, or ``None`` when ungoverned."""
        return self.owners.get(namespace)

    def resolve(self, namespace: str, *, asserting_hubs: Iterable[str] = ()) -> OwnershipDecision:
        """Resolve which hub may grant claims in ``namespace``, fail-closed on doubt.

        The configured owner is the authority. Any hub in ``asserting_hubs`` that is not that
        owner contests ownership and forces :data:`~OwnershipOutcome.PARTITIONED`, so a real or
        suspected split refuses every grant until ownership is re-established. An ungoverned
        namespace refuses too. Otherwise the namespace resolves local (the resolving hub owns
        it) or remote (a named peer owns it, and the claim must be routed there).

        Parameters
        ----------
        namespace : str
            The namespace a claim concerns.
        asserting_hubs : Iterable[str], optional
            Hub ids observed acting as the namespace's owner at runtime (for example peers seen
            granting claims in it). The resolving hub's own id is ignored. Any other id beyond
            the configured owner contests ownership.

        Returns
        -------
        OwnershipDecision
            The resolution; :attr:`OwnershipDecision.grants_locally` is ``True`` only for a hub
            that authoritatively and uncontestedly owns the namespace.
        """
        owner = self.owners.get(namespace)
        if owner is None:
            return OwnershipDecision(outcome=OwnershipOutcome.UNGOVERNED, namespace=namespace)
        contesting = tuple(
            sorted(hub for hub in asserting_hubs if hub != owner and hub != self.local_hub_id)
        )
        if contesting:
            return OwnershipDecision(
                outcome=OwnershipOutcome.PARTITIONED, namespace=namespace, contesting=contesting
            )
        if owner == self.local_hub_id:
            return OwnershipDecision(
                outcome=OwnershipOutcome.LOCAL, namespace=namespace, owner_hub_id=owner
            )
        return OwnershipDecision(
            outcome=OwnershipOutcome.REMOTE, namespace=namespace, owner_hub_id=owner
        )

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the federated trust bundle: deny-by-default cross-domain policy
"""The federated trust bundle — deny-by-default policy for peered Synapse domains.

A **trust domain** is one operator's deployment: the namespaces it owns, the agent
identities it issues, and the signing keys and certificate pins it manages
(`docs/federated-trust-model.md`). This module extends the single-host
:class:`~synapse_channel.core.tls.MTLSTrustedPeer` notion from "trusted peer hosts" to
"trusted peer **domains**": a :class:`FederationPeer` records, per remote domain, the
local namespaces it may address, the certificate pins and event-signing key ids it is
accepted under, the bounded local scope its subjects map to, and an expiry plus a
revocation flag.

Federation is **deny-by-default**: a remote domain addresses nothing until an operator
grants it, and authority is always resolved by the issuing domain, never trusted from
the asserted content. The bundle here is the *policy* half — pure, I/O-free, no crypto
of its own. It decides what a peering permits; it **composes** with, and never weakens,
the checks that already exist — mutual TLS pin verification, Ed25519 event-signature
verification, and the local ACL. :func:`compose_cross_domain` expresses that law: a
cross-domain frame is allowed only when the federation policy *and* every external check
allow it, so a frame any layer rejects is rejected.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synapse_channel.core.acl import Target


class FederationDenyReason:
    """Reasons a cross-domain authorisation is refused (deny-by-default)."""

    UNKNOWN_DOMAIN = "unknown_domain"
    REVOKED_PEERING = "revoked_peering"
    EXPIRED_PEERING = "expired_peering"
    NAMESPACE_NOT_GRANTED = "namespace_not_granted"
    SIGNING_KEY_NOT_ACCEPTED = "signing_key_not_accepted"
    CERTIFICATE_PIN_NOT_ACCEPTED = "certificate_pin_not_accepted"


AUTHORISED = "authorised"
"""Reason string on an allowed decision."""


@dataclass(frozen=True)
class ScopeGrant:
    """A single bounded permission a remote subject is mapped to: one verb in one namespace."""

    verb: str
    namespace: str


@dataclass(frozen=True)
class FederationPeer:
    """A trusted remote domain and the bounded local access its peering grants.

    Attributes
    ----------
    domain_id : str
        Stable id of the remote domain; every federated statement is attributable to it.
    namespaces : frozenset[str]
        Local project namespaces the remote domain may address (deny-by-default).
    certificate_pins : frozenset[str]
        Accepted remote-hub certificate SHA-256 pins (``sha256:<hex>``).
    signing_key_ids : frozenset[str]
        Accepted Ed25519 event-signing key ids for the remote domain.
    scope_grants : tuple[ScopeGrant, ...]
        The bounded local scope a remote subject maps to — specific verbs over specific
        namespaces. Empty means read nothing; a remote subject inherits no local default.
    expires_at : float or None
        Monotonic time the peering expires; ``None`` never expires by time.
    revoked : bool
        When ``True`` the whole peering is refused.
    """

    domain_id: str
    namespaces: frozenset[str] = frozenset()
    certificate_pins: frozenset[str] = frozenset()
    signing_key_ids: frozenset[str] = frozenset()
    scope_grants: tuple[ScopeGrant, ...] = ()
    expires_at: float | None = None
    revoked: bool = False

    def is_active(self, now: float) -> bool:
        """Return whether the peering is neither revoked nor expired at ``now``."""
        return not self.revoked and (self.expires_at is None or now < self.expires_at)

    def grants_for(self, namespace: str) -> tuple[ScopeGrant, ...]:
        """Return the bounded verbs this peering grants in ``namespace``, in order."""
        return tuple(grant for grant in self.scope_grants if grant.namespace == namespace)


@dataclass(frozen=True)
class FederationDecision:
    """The outcome of a federation-policy authorisation for one cross-domain frame.

    Attributes
    ----------
    allowed : bool
        Whether the federation policy permits the frame (before the external checks).
    domain_id : str
        The issuing domain the decision is attributed to.
    reason : str
        :data:`AUTHORISED`, or a :class:`FederationDenyReason` value.
    scope : tuple[ScopeGrant, ...]
        The bounded local scope granted when allowed; empty on a deny.
    """

    allowed: bool
    domain_id: str
    reason: str
    scope: tuple[ScopeGrant, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible mapping of the decision."""
        return {
            "allowed": self.allowed,
            "domain_id": self.domain_id,
            "reason": self.reason,
            "scope": [{"verb": g.verb, "namespace": g.namespace} for g in self.scope],
        }


class FederationBundle:
    """A set of operator-confirmed peer domains and the deny-by-default policy over them.

    The bundle owns the *federation* checks (domain peered, not revoked or expired,
    namespace granted, signing key accepted, certificate pin accepted) and the scope
    mapping. It owns no crypto; :func:`compose_cross_domain` joins its decision with the
    external mTLS, signature, and ACL results.
    """

    def __init__(self, peers: Iterable[FederationPeer] = ()) -> None:
        self._peers: dict[str, FederationPeer] = {peer.domain_id: peer for peer in peers}

    def peer(self, domain_id: str) -> FederationPeer | None:
        """Return the peer for ``domain_id``, or ``None`` when it is not peered."""
        return self._peers.get(domain_id)

    def domains(self) -> tuple[str, ...]:
        """Return the peered domain ids, sorted."""
        return tuple(sorted(self._peers))

    def authorise(
        self,
        domain_id: str,
        *,
        namespace: str,
        signing_key_id: str,
        certificate_pin: str,
        now: float,
    ) -> FederationDecision:
        """Decide whether the federation policy permits a cross-domain frame.

        The checks run deny-by-default in order: the domain must be peered, the peering
        active (not revoked or expired), the namespace granted, the signing key accepted,
        and the certificate pin accepted. The first failure returns its reason; otherwise
        the frame is authorised with the bounded scope mapped for the namespace. This is
        only the federation gate — the caller still composes it with mutual TLS, event
        signature, and the local ACL via :func:`compose_cross_domain`.
        """
        peer = self._peers.get(domain_id)
        if peer is None:
            return self._deny(domain_id, FederationDenyReason.UNKNOWN_DOMAIN)
        if peer.revoked:
            return self._deny(domain_id, FederationDenyReason.REVOKED_PEERING)
        if not peer.is_active(now):
            return self._deny(domain_id, FederationDenyReason.EXPIRED_PEERING)
        if namespace not in peer.namespaces:
            return self._deny(domain_id, FederationDenyReason.NAMESPACE_NOT_GRANTED)
        if signing_key_id not in peer.signing_key_ids:
            return self._deny(domain_id, FederationDenyReason.SIGNING_KEY_NOT_ACCEPTED)
        if certificate_pin not in peer.certificate_pins:
            return self._deny(domain_id, FederationDenyReason.CERTIFICATE_PIN_NOT_ACCEPTED)
        return FederationDecision(
            allowed=True,
            domain_id=domain_id,
            reason=AUTHORISED,
            scope=peer.grants_for(namespace),
        )

    @staticmethod
    def _deny(domain_id: str, reason: str) -> FederationDecision:
        return FederationDecision(allowed=False, domain_id=domain_id, reason=reason)


def resolve_domain(
    bundle: FederationBundle,
    *,
    key_id: str,
    certificate_pin: str,
) -> str | None:
    """Resolve the peered domain a frame belongs to, from verified credentials only.

    A cross-domain frame is identified, and its issuing domain resolved, from material
    the hub has already verified — never a self-declared field. The caller supplies the
    Ed25519 ``key_id`` taken from the *verified* ``signature.key_id`` and the
    ``certificate_pin`` read off the *live* mutual-TLS socket. A peering owns the frame
    only when it accepts **both**: the same domain must enumerate the signing key and the
    certificate pin, so a real key presented over a different domain's connection resolves
    to neither (fail-closed).

    Parameters
    ----------
    bundle : FederationBundle
        The operator-confirmed peerings to resolve against.
    key_id : str
        The verified Ed25519 signing-key id the frame was signed with.
    certificate_pin : str
        The ``sha256:<hex>`` pin of the live peer certificate.

    Returns
    -------
    str or None
        The single peered ``domain_id`` that accepts both the key id and the pin; ``None``
        when no peering accepts both (a local or unpeered frame) or when more than one does
        (a misconfiguration, refused deny-closed rather than guessed).
    """
    matches = [
        peer.domain_id
        for peer in bundle._peers.values()
        if key_id in peer.signing_key_ids and certificate_pin in peer.certificate_pins
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def signing_key_is_peered(bundle: FederationBundle, key_id: str) -> bool:
    """Return whether any peering enrols ``key_id`` as a remote signing key.

    A frame signed with a peered key claims cross-domain authority even when its
    connection cannot be pinned; the gate uses this predicate to refuse such a
    frame outright rather than letting it degrade to the local path the way a
    frame signed with a purely local key does.
    """
    return any(key_id in peer.signing_key_ids for peer in bundle._peers.values())


class DomainResolutionDiagnosis:
    """Why a ``(key_id, certificate_pin)`` pair resolved to no single peered domain.

    :func:`resolve_domain` fails closed and returns ``None`` for several distinct
    situations that read identically to a caller. Most are ordinary — a frame signed
    with a local key over a peer's connection is not cross-domain and is meant to take
    the local path. But some are operator misconfigurations that otherwise leave no
    signal: a peer whose certificate pin was never enrolled, whose signing key was
    never enrolled, whose key and pin were enrolled under *different* peerings, or a
    pair that two peerings both claim. This vocabulary lets the caller warn on the
    misconfigurations and stay silent on the ordinary local frame.
    """

    RESOLVED = "resolved"
    """A single peering accepts both — :func:`resolve_domain` would return a domain
    (never produced for an unresolved pair; present so the classification is total)."""

    UNRELATED = "unrelated"
    """Neither the key nor the pin is enrolled in any peering — an ordinary local or
    foreign frame, not a misconfiguration. Callers stay silent on this."""

    AMBIGUOUS = "ambiguous_multiple_peerings"
    """Two or more peerings each enrol both the key and the pin — overlapping peerings."""

    KEY_WITHOUT_PIN = "signing_key_enrolled_but_certificate_pin_unknown"
    """The signing key is enrolled somewhere, but the certificate pin is enrolled nowhere
    — the peer's certificate pin is missing or stale in the bundle."""

    PIN_WITHOUT_KEY = "certificate_pin_enrolled_but_signing_key_unknown"
    """The certificate pin is enrolled somewhere, but the signing key is enrolled nowhere
    — the peer's signing key id is missing or stale in the bundle."""

    SPLIT_ACROSS_PEERINGS = "signing_key_and_certificate_pin_in_different_peerings"
    """Both the key and the pin are enrolled, but never together in one peering — the
    peer's credentials are split across two peerings and neither alone owns the frame."""


def diagnose_unresolved_domain(
    bundle: FederationBundle,
    *,
    key_id: str,
    certificate_pin: str,
) -> str:
    """Classify *why* ``(key_id, certificate_pin)`` resolved to no single peered domain.

    Companion to :func:`resolve_domain` for observability: it turns a fail-closed
    ``None`` into an operator-actionable reason so a misconfigured peering does not go
    silent, while an ordinary local frame stays quiet. The classification is total, so a
    caller can log the misconfiguration reasons and skip
    :attr:`~DomainResolutionDiagnosis.UNRELATED` (and the never-produced
    :attr:`~DomainResolutionDiagnosis.RESOLVED`).

    Parameters
    ----------
    bundle : FederationBundle
        The operator-confirmed peerings the pair failed to resolve against.
    key_id : str
        The verified Ed25519 signing-key id the frame was signed with.
    certificate_pin : str
        The ``sha256:<hex>`` pin of the live peer certificate.

    Returns
    -------
    str
        A :class:`DomainResolutionDiagnosis` value naming the reason.
    """
    both = [
        peer.domain_id
        for peer in bundle._peers.values()
        if key_id in peer.signing_key_ids and certificate_pin in peer.certificate_pins
    ]
    if len(both) >= 2:
        return DomainResolutionDiagnosis.AMBIGUOUS
    if len(both) == 1:
        return DomainResolutionDiagnosis.RESOLVED
    key_known = any(key_id in peer.signing_key_ids for peer in bundle._peers.values())
    pin_known = any(certificate_pin in peer.certificate_pins for peer in bundle._peers.values())
    if key_known and pin_known:
        return DomainResolutionDiagnosis.SPLIT_ACROSS_PEERINGS
    if key_known:
        return DomainResolutionDiagnosis.KEY_WITHOUT_PIN
    if pin_known:
        return DomainResolutionDiagnosis.PIN_WITHOUT_KEY
    return DomainResolutionDiagnosis.UNRELATED


def scope_authorises(
    accesses: list[tuple[str, Target]],
    *,
    scope: tuple[ScopeGrant, ...],
    namespace: str,
) -> bool:
    """Return whether a peering's bounded scope authorises every access a frame needs.

    A remote subject is evaluated against the peering's mapped scope exactly as a local
    subject is evaluated against the local ACL: the frame's required accesses
    (:func:`~synapse_channel.core.acl_enforcement.required_accesses`, one
    ``(permission, target)`` each) are mapped to ``(verb, namespace)`` — the verb is the
    ACL permission constant the peering's :class:`ScopeGrant` reuses, and the namespace is
    the single remote namespace the frame acts in — and **every** one must match a grant in
    ``scope``. A remote subject inherits no local default, so an empty ``scope`` authorises
    nothing, and a frame that maps to no access (a read, or an unmapped mutation) is denied
    rather than silently allowed — the same fail-closed posture as
    :func:`~synapse_channel.core.acl_enforcement.authorise_frame`.

    Parameters
    ----------
    accesses : list[tuple[str, Target]]
        The frame's required accesses, as ``required_accesses`` returns them.
    scope : tuple[ScopeGrant, ...]
        The bounded scope the peering maps the remote subject to.
    namespace : str
        The local namespace the remote subject is acting in (``project_of(sender)``).

    Returns
    -------
    bool
        ``True`` only when ``accesses`` is non-empty and every access's permission is
        granted in ``namespace`` by ``scope``; ``False`` otherwise (deny-closed).
    """
    if not accesses:
        return False
    granted = {(grant.verb, grant.namespace) for grant in scope}
    return all((permission, namespace) in granted for permission, _target in accesses)


def compose_cross_domain(
    decision: FederationDecision,
    *,
    mtls_ok: bool,
    signature_ok: bool,
    acl_ok: bool,
) -> bool:
    """Return whether a cross-domain frame is permitted by *all* layers.

    Federation never weakens a check; it only refuses to widen one. A frame is allowed
    only when the federation policy decision and every external check — mutual TLS peer
    verification, event-signature verification, and the local ACL for the mapped scope —
    all allow it. Any layer rejecting the frame rejects it.
    """
    return decision.allowed and mtls_ok and signature_ok and acl_ok


def peering_can_authorise(peer: FederationPeer, *, now: float) -> bool:
    """Return whether ``peer``, as configured, could authorise any cross-domain frame.

    A peering can only ever compose to an allow when it is active at ``now``, accepts
    at least one signing key and one certificate pin, and maps at least one scope grant
    inside a namespace it also grants (:meth:`FederationPeer.grants_for` filters grants
    by namespace, and :meth:`FederationBundle.authorise` checks the namespace before the
    scope, so a grant outside ``peer.namespaces`` is unreachable). A peering failing any
    of these conditions is observe-only by construction: every frame it resolves is
    refused deny-closed whatever the rest of the hub configuration does.

    Parameters
    ----------
    peer : FederationPeer
        The operator-confirmed peering to inspect.
    now : float
        POSIX timestamp (``time.time()``) the activity window is evaluated at.

    Returns
    -------
    bool
        ``True`` when at least one cross-domain access could compose to an allow.
    """
    if not peer.is_active(now):
        return False
    if not peer.signing_key_ids or not peer.certificate_pins:
        return False
    return any(grant.namespace in peer.namespaces for grant in peer.scope_grants)


def bundle_can_authorise(bundle: FederationBundle, *, now: float) -> bool:
    """Return whether any peering in ``bundle`` could authorise a cross-domain frame.

    The hub start-up gate uses this to distinguish a store whose peerings claim
    enforceable cross-domain scope — which demands per-message authentication, since
    without it no signing key is ever verified and the claimed scope is unenforceable —
    from a store that is observe-only by construction (revoked, expired, credential-less,
    or scope-less peerings) and safe to load for diagnostics alone.

    Parameters
    ----------
    bundle : FederationBundle
        The composed peer set to inspect.
    now : float
        POSIX timestamp (``time.time()``) the activity windows are evaluated at.

    Returns
    -------
    bool
        ``True`` when at least one peering satisfies :func:`peering_can_authorise`.
    """
    return any(
        peering_can_authorise(peer, now=now)
        for domain in bundle.domains()
        if (peer := bundle.peer(domain)) is not None
    )

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federated trust bundle regressions

from __future__ import annotations

from synapse_channel.core.acl import BOARD, CLAIM, MESSAGE, RELEASE, Target
from synapse_channel.core.federation import (
    AUTHORISED,
    DomainResolutionDiagnosis,
    FederationBundle,
    FederationDenyReason,
    FederationPeer,
    ScopeGrant,
    compose_cross_domain,
    diagnose_unresolved_domain,
    resolve_domain,
    scope_authorises,
)


def _peer(**overrides: object) -> FederationPeer:
    base: dict[str, object] = {
        "domain_id": "acme",
        "namespaces": frozenset({"acme/shared"}),
        "certificate_pins": frozenset({"sha256:aa"}),
        "signing_key_ids": frozenset({"key-1"}),
        "scope_grants": (
            ScopeGrant("read_board", "acme/shared"),
            ScopeGrant("post_chat", "acme/shared"),
            ScopeGrant("read_board", "acme/other"),
        ),
        "expires_at": None,
        "revoked": False,
    }
    base.update(overrides)
    return FederationPeer(**base)  # type: ignore[arg-type]


def _ok_bundle() -> FederationBundle:
    return FederationBundle([_peer()])


_OK = {"namespace": "acme/shared", "signing_key_id": "key-1", "certificate_pin": "sha256:aa"}


def test_is_active_honours_revocation_and_expiry() -> None:
    assert _peer().is_active(100.0) is True  # no expiry, not revoked
    assert _peer(revoked=True).is_active(100.0) is False
    assert _peer(expires_at=200.0).is_active(100.0) is True  # before expiry
    assert _peer(expires_at=50.0).is_active(100.0) is False  # past expiry


def test_grants_for_filters_to_the_namespace() -> None:
    grants = _peer().grants_for("acme/shared")
    assert [g.verb for g in grants] == ["read_board", "post_chat"]
    assert _peer().grants_for("acme/other") == (ScopeGrant("read_board", "acme/other"),)
    assert _peer().grants_for("unknown") == ()


def test_bundle_lookup_and_domains() -> None:
    bundle = FederationBundle([_peer(), _peer(domain_id="globex")])
    assert bundle.domains() == ("acme", "globex")
    assert bundle.peer("acme") is not None
    assert bundle.peer("missing") is None
    assert FederationBundle().domains() == ()  # empty default


def test_authorise_allows_a_fully_satisfied_frame_with_bounded_scope() -> None:
    decision = _ok_bundle().authorise("acme", now=100.0, **_OK)
    assert decision.allowed is True
    assert decision.reason == AUTHORISED
    assert decision.domain_id == "acme"
    # only the verbs granted in the requested namespace
    assert [g.verb for g in decision.scope] == ["read_board", "post_chat"]


def test_authorise_is_deny_by_default_in_order() -> None:
    bundle = _ok_bundle()
    # unknown domain
    assert bundle.authorise("nope", now=100.0, **_OK).reason == FederationDenyReason.UNKNOWN_DOMAIN
    # revoked
    rev = FederationBundle([_peer(revoked=True)])
    assert rev.authorise("acme", now=100.0, **_OK).reason == FederationDenyReason.REVOKED_PEERING
    # expired
    exp = FederationBundle([_peer(expires_at=50.0)])
    assert exp.authorise("acme", now=100.0, **_OK).reason == FederationDenyReason.EXPIRED_PEERING
    # namespace not granted
    ns = bundle.authorise(
        "acme",
        namespace="acme/secret",
        signing_key_id="key-1",
        certificate_pin="sha256:aa",
        now=100.0,
    )
    assert ns.reason == FederationDenyReason.NAMESPACE_NOT_GRANTED
    assert ns.allowed is False and ns.scope == ()
    # signing key not accepted
    key = bundle.authorise(
        "acme",
        namespace="acme/shared",
        signing_key_id="bad",
        certificate_pin="sha256:aa",
        now=100.0,
    )
    assert key.reason == FederationDenyReason.SIGNING_KEY_NOT_ACCEPTED
    # certificate pin not accepted
    pin = bundle.authorise(
        "acme",
        namespace="acme/shared",
        signing_key_id="key-1",
        certificate_pin="sha256:bad",
        now=100.0,
    )
    assert pin.reason == FederationDenyReason.CERTIFICATE_PIN_NOT_ACCEPTED


def test_decision_to_dict_round_trips() -> None:
    decision = _ok_bundle().authorise("acme", now=100.0, **_OK)
    assert decision.to_dict() == {
        "allowed": True,
        "domain_id": "acme",
        "reason": AUTHORISED,
        "scope": [
            {"verb": "read_board", "namespace": "acme/shared"},
            {"verb": "post_chat", "namespace": "acme/shared"},
        ],
    }


def test_compose_requires_every_layer_to_allow() -> None:
    allowed = _ok_bundle().authorise("acme", now=100.0, **_OK)
    denied = _ok_bundle().authorise("nope", now=100.0, **_OK)
    # all layers allow -> permitted
    assert compose_cross_domain(allowed, mtls_ok=True, signature_ok=True, acl_ok=True) is True
    # federation never widens: any single failing layer rejects the frame
    assert compose_cross_domain(allowed, mtls_ok=False, signature_ok=True, acl_ok=True) is False
    assert compose_cross_domain(allowed, mtls_ok=True, signature_ok=False, acl_ok=True) is False
    assert compose_cross_domain(allowed, mtls_ok=True, signature_ok=True, acl_ok=False) is False
    # a denied federation policy cannot be rescued by passing external checks
    assert compose_cross_domain(denied, mtls_ok=True, signature_ok=True, acl_ok=True) is False


def test_resolve_domain_matches_a_single_peer_on_key_and_pin() -> None:
    bundle = FederationBundle([_peer()])
    assert resolve_domain(bundle, key_id="key-1", certificate_pin="sha256:aa") == "acme"


def test_resolve_domain_requires_the_same_peer_to_accept_both() -> None:
    # acme accepts key-1/sha256:aa; globex accepts key-2/sha256:bb. A key from one
    # presented over the other's connection resolves to neither (fail-closed).
    bundle = FederationBundle(
        [
            _peer(),
            _peer(
                domain_id="globex",
                signing_key_ids=frozenset({"key-2"}),
                certificate_pins=frozenset({"sha256:bb"}),
            ),
        ]
    )
    assert resolve_domain(bundle, key_id="key-1", certificate_pin="sha256:bb") is None
    assert resolve_domain(bundle, key_id="key-2", certificate_pin="sha256:aa") is None
    # each peer still resolves on its own matched pair
    assert resolve_domain(bundle, key_id="key-1", certificate_pin="sha256:aa") == "acme"
    assert resolve_domain(bundle, key_id="key-2", certificate_pin="sha256:bb") == "globex"


def test_resolve_domain_returns_none_when_unpeered() -> None:
    bundle = FederationBundle([_peer()])
    # a key/pin no peering enumerates -> not cross-domain (a local frame)
    assert resolve_domain(bundle, key_id="local-key", certificate_pin="sha256:aa") is None
    assert resolve_domain(bundle, key_id="key-1", certificate_pin="sha256:zz") is None
    # an empty bundle peers nothing
    assert resolve_domain(FederationBundle(), key_id="key-1", certificate_pin="sha256:aa") is None


def test_resolve_domain_is_deny_closed_on_ambiguity() -> None:
    # two peerings accept the same key_id+pin -> a misconfiguration, refused not guessed.
    bundle = FederationBundle([_peer(), _peer(domain_id="globex")])
    assert resolve_domain(bundle, key_id="key-1", certificate_pin="sha256:aa") is None


def test_diagnose_resolved_when_a_single_peering_owns_the_pair() -> None:
    # the total classification names the resolvable case (never emitted by the hub path)
    bundle = FederationBundle([_peer()])
    assert (
        diagnose_unresolved_domain(bundle, key_id="key-1", certificate_pin="sha256:aa")
        == DomainResolutionDiagnosis.RESOLVED
    )


def test_diagnose_unrelated_when_neither_credential_is_enrolled() -> None:
    # a local frame: neither the key nor the pin appears in any peering -> silent, ordinary
    bundle = FederationBundle([_peer()])
    assert (
        diagnose_unresolved_domain(bundle, key_id="local-key", certificate_pin="sha256:zz")
        == DomainResolutionDiagnosis.UNRELATED
    )
    assert (
        diagnose_unresolved_domain(FederationBundle(), key_id="key-1", certificate_pin="sha256:aa")
        == DomainResolutionDiagnosis.UNRELATED
    )


def test_diagnose_key_without_pin_flags_a_stale_or_missing_certificate() -> None:
    # the signing key is enrolled but the presented pin is enrolled nowhere
    bundle = FederationBundle([_peer()])
    assert (
        diagnose_unresolved_domain(bundle, key_id="key-1", certificate_pin="sha256:zz")
        == DomainResolutionDiagnosis.KEY_WITHOUT_PIN
    )


def test_diagnose_pin_without_key_flags_a_stale_or_missing_signing_key() -> None:
    # the certificate pin is enrolled but the presented signing key is enrolled nowhere
    bundle = FederationBundle([_peer()])
    assert (
        diagnose_unresolved_domain(bundle, key_id="unknown-key", certificate_pin="sha256:aa")
        == DomainResolutionDiagnosis.PIN_WITHOUT_KEY
    )


def test_diagnose_split_across_peerings_flags_credentials_in_different_peers() -> None:
    # key-1 lives in acme, sha256:bb lives in globex; no single peering owns both
    bundle = FederationBundle(
        [
            _peer(),
            _peer(
                domain_id="globex",
                signing_key_ids=frozenset({"key-2"}),
                certificate_pins=frozenset({"sha256:bb"}),
            ),
        ]
    )
    assert (
        diagnose_unresolved_domain(bundle, key_id="key-1", certificate_pin="sha256:bb")
        == DomainResolutionDiagnosis.SPLIT_ACROSS_PEERINGS
    )


def test_diagnose_ambiguous_when_two_peerings_own_the_same_pair() -> None:
    # two peerings each enrol the same key+pin -> overlapping peerings, deny-closed
    bundle = FederationBundle([_peer(), _peer(domain_id="globex")])
    assert (
        diagnose_unresolved_domain(bundle, key_id="key-1", certificate_pin="sha256:aa")
        == DomainResolutionDiagnosis.AMBIGUOUS
    )


_GRANTS = (ScopeGrant(MESSAGE, "acme/shared"), ScopeGrant(CLAIM, "acme/shared"))


def test_scope_authorises_allows_every_access_within_scope() -> None:
    # a single mapped access whose verb+namespace is granted
    assert (
        scope_authorises(
            [(MESSAGE, Target("channel", "general"))],
            scope=_GRANTS,
            namespace="acme/shared",
        )
        is True
    )
    # several accesses, each granted (a claim with its task-id and a path access)
    accesses = [(CLAIM, Target("claim", "T-1")), (CLAIM, Target("path", "src/"))]
    assert scope_authorises(accesses, scope=_GRANTS, namespace="acme/shared") is True


def test_scope_authorises_denies_a_verb_outside_scope() -> None:
    # RELEASE is not granted by _GRANTS
    assert (
        scope_authorises(
            [(RELEASE, Target("claim", "T-1"))],
            scope=_GRANTS,
            namespace="acme/shared",
        )
        is False
    )
    # one granted access plus one ungranted -> all must hold, so denied
    mixed = [(MESSAGE, Target("channel", "general")), (BOARD, Target("board", "*"))]
    assert scope_authorises(mixed, scope=_GRANTS, namespace="acme/shared") is False


def test_scope_authorises_denies_a_granted_verb_in_another_namespace() -> None:
    # the verb is granted, but only in acme/shared; the frame acts in acme/other
    assert (
        scope_authorises(
            [(MESSAGE, Target("channel", "general"))],
            scope=_GRANTS,
            namespace="acme/other",
        )
        is False
    )


def test_scope_authorises_is_deny_closed_on_empty_scope_or_accesses() -> None:
    # a remote subject inherits no local default: empty scope authorises nothing
    assert (
        scope_authorises(
            [(MESSAGE, Target("channel", "general"))],
            scope=(),
            namespace="acme/shared",
        )
        is False
    )
    # a frame mapping to no access (a read, or an unmapped mutation) is denied, not allowed
    assert scope_authorises([], scope=_GRANTS, namespace="acme/shared") is False

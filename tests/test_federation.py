# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federated trust bundle regressions

from __future__ import annotations

from synapse_channel.core.federation import (
    AUTHORISED,
    FederationBundle,
    FederationDenyReason,
    FederationPeer,
    ScopeGrant,
    compose_cross_domain,
    resolve_domain,
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

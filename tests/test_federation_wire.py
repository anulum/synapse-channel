# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation-exchange wire codec and fingerprint regressions

from __future__ import annotations

import dataclasses
import hashlib
import json

import pytest

from synapse_channel.core.federation import FederationPeer, ScopeGrant
from synapse_channel.core.federation_store import peer_to_dict
from synapse_channel.core.federation_wire import (
    FederationWireError,
    bundle_fingerprint,
    decode_federation_offer,
    encode_federation_offer,
    render_offer_fingerprints,
)

_PEER = FederationPeer(
    domain_id="lab-a",
    namespaces=frozenset({"lab-a/shared", "lab-a/ops"}),
    certificate_pins=frozenset({"sha256:aa", "sha256:bb"}),
    signing_key_ids=frozenset({"key-2", "key-1"}),
    scope_grants=(ScopeGrant("read_board", "lab-a/shared"),),
    expires_at=1_700_000_000.0,
    revoked=False,
)


class TestOfferCodec:
    def test_encode_is_the_import_bundle_format(self) -> None:
        assert encode_federation_offer(_PEER) == peer_to_dict(_PEER)

    def test_round_trip_preserves_the_peer(self) -> None:
        assert decode_federation_offer(encode_federation_offer(_PEER)) == _PEER

    def test_decode_keeps_deny_by_default_omissions(self) -> None:
        peer = decode_federation_offer({"domain_id": "bare"})
        assert peer.namespaces == frozenset()
        assert peer.certificate_pins == frozenset()
        assert peer.signing_key_ids == frozenset()
        assert peer.scope_grants == ()

    @pytest.mark.parametrize("raw", [None, "text", 7, ["domain_id"], True])
    def test_decode_rejects_a_non_mapping_body(self, raw: object) -> None:
        with pytest.raises(FederationWireError, match="must be a JSON object"):
            decode_federation_offer(raw)

    def test_decode_rejects_a_missing_domain(self) -> None:
        with pytest.raises(FederationWireError, match="malformed federation offer"):
            decode_federation_offer({"namespaces": ["lab-a/shared"]})

    def test_decode_rejects_a_malformed_field(self) -> None:
        with pytest.raises(FederationWireError, match="malformed federation offer"):
            decode_federation_offer({"domain_id": "lab-a", "namespaces": "not-a-list"})

    def test_decode_rejects_a_non_numeric_expiry(self) -> None:
        with pytest.raises(FederationWireError, match="malformed federation offer"):
            decode_federation_offer({"domain_id": "lab-a", "expires_at": "soon"})

    def test_decode_rejects_an_unconvertible_expiry(self) -> None:
        with pytest.raises(FederationWireError, match="malformed federation offer"):
            decode_federation_offer({"domain_id": "lab-a", "expires_at": [1.0]})


class TestBundleFingerprint:
    def test_fingerprint_is_the_canonical_bundle_digest(self) -> None:
        canonical = json.dumps(peer_to_dict(_PEER), sort_keys=True, separators=(",", ":"))
        expected = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert bundle_fingerprint(_PEER) == expected

    def test_fingerprint_is_deterministic(self) -> None:
        assert bundle_fingerprint(_PEER) == bundle_fingerprint(_PEER)

    def test_fingerprint_survives_a_wire_round_trip(self) -> None:
        fetched = decode_federation_offer(encode_federation_offer(_PEER))
        assert bundle_fingerprint(fetched) == bundle_fingerprint(_PEER)

    @pytest.mark.parametrize(
        "altered",
        [
            dataclasses.replace(_PEER, domain_id="lab-b"),
            dataclasses.replace(_PEER, namespaces=_PEER.namespaces | {"lab-a/extra"}),
            dataclasses.replace(_PEER, certificate_pins=frozenset({"sha256:aa"})),
            dataclasses.replace(_PEER, signing_key_ids=frozenset({"key-1", "key-3"})),
            dataclasses.replace(
                _PEER,
                scope_grants=(*_PEER.scope_grants, ScopeGrant("claim_task", "lab-a/ops")),
            ),
            dataclasses.replace(_PEER, expires_at=None),
            dataclasses.replace(_PEER, revoked=True),
        ],
        ids=["domain", "namespace", "pin", "key", "scope", "expiry", "revoked"],
    )
    def test_any_policy_change_changes_the_fingerprint(self, altered: FederationPeer) -> None:
        assert bundle_fingerprint(altered) != bundle_fingerprint(_PEER)


class TestRenderOfferFingerprints:
    def test_full_bundle_rendering(self) -> None:
        block = render_offer_fingerprints(_PEER)
        lines = block.splitlines()
        assert lines[0] == "domain:             lab-a"
        assert "signing key ids:    key-1" in lines
        assert f"{'':<20}key-2" in lines
        assert "certificate pins:   sha256:aa" in lines
        assert f"{'':<20}sha256:bb" in lines
        assert "namespaces:         lab-a/ops, lab-a/shared" in lines
        assert "scope grants:       1" in lines
        assert "expires:            2023-11-14T22:13:20Z" in lines
        assert lines[-1] == f"bundle fingerprint: {bundle_fingerprint(_PEER)}"

    def test_empty_collections_render_as_none(self) -> None:
        block = render_offer_fingerprints(FederationPeer(domain_id="bare"))
        assert "signing key ids:    (none)" in block
        assert "certificate pins:   (none)" in block
        assert "namespaces:         (none)" in block
        assert "expires:            never" in block

    def test_revoked_bundle_is_flagged(self) -> None:
        revoked = dataclasses.replace(_PEER, revoked=True)
        assert "revoked:            yes" in render_offer_fingerprints(revoked)
        assert "revoked:" not in render_offer_fingerprints(_PEER)

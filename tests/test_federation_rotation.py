# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — federation bundle rotation: fresh expiry, grace-window key material

from __future__ import annotations

import pytest

from synapse_channel.core.federation import FederationPeer, ScopeGrant
from synapse_channel.core.federation_rotation import (
    DEFAULT_ROTATION_LIFETIME_DAYS,
    SECONDS_PER_DAY,
    FederationRotationError,
    rotate_bundle,
)


def _peer(**over: object) -> FederationPeer:
    base: dict[str, object] = {
        "domain_id": "acme",
        "namespaces": frozenset({"acme/shared"}),
        "certificate_pins": frozenset({"sha256:aa"}),
        "signing_key_ids": frozenset({"key-1"}),
        "scope_grants": (ScopeGrant(verb="read_board", namespace="acme/shared"),),
        "expires_at": 1000.0,
        "revoked": False,
    }
    base.update(over)
    return FederationPeer(**base)  # type: ignore[arg-type]


def test_rotate_bumps_the_expiry_to_now_plus_lifetime() -> None:
    peer = _peer(expires_at=1000.0)
    rotated, summary = rotate_bundle(peer, now=5000.0, lifetime_seconds=90 * SECONDS_PER_DAY)
    assert rotated.expires_at == 5000.0 + 90 * SECONDS_PER_DAY
    assert summary.previous_expires_at == 1000.0
    assert summary.expires_at == rotated.expires_at


def test_a_never_expiring_bundle_reports_previous_expiry_as_none() -> None:
    peer = _peer(expires_at=None)
    _, summary = rotate_bundle(peer, now=0.0, lifetime_seconds=1.0)
    assert summary.previous_expires_at is None


def test_an_added_signing_key_is_kept_alongside_the_old_one() -> None:
    peer = _peer(signing_key_ids=frozenset({"key-1"}))
    rotated, summary = rotate_bundle(
        peer, now=0.0, lifetime_seconds=1.0, add_signing_keys=["key-2"]
    )
    assert rotated.signing_key_ids == frozenset({"key-1", "key-2"})
    assert summary.signing_keys.added == ("key-2",)
    assert summary.signing_keys.retained == ("key-1",)
    assert summary.signing_keys.retired == ()


def test_an_added_certificate_pin_is_unioned() -> None:
    peer = _peer(certificate_pins=frozenset({"sha256:aa"}))
    rotated, summary = rotate_bundle(peer, now=0.0, lifetime_seconds=1.0, add_pins=["sha256:bb"])
    assert rotated.certificate_pins == frozenset({"sha256:aa", "sha256:bb"})
    assert summary.certificate_pins.added == ("sha256:bb",)


def test_retiring_a_signing_key_drops_it() -> None:
    peer = _peer(signing_key_ids=frozenset({"key-1", "key-2"}))
    rotated, summary = rotate_bundle(
        peer, now=0.0, lifetime_seconds=1.0, retire_signing_keys=["key-1"]
    )
    assert rotated.signing_key_ids == frozenset({"key-2"})
    assert summary.signing_keys.retired == ("key-1",)
    assert summary.signing_keys.retained == ("key-2",)


def test_retiring_a_certificate_pin_drops_it() -> None:
    peer = _peer(certificate_pins=frozenset({"sha256:aa", "sha256:bb"}))
    rotated, _ = rotate_bundle(peer, now=0.0, lifetime_seconds=1.0, retire_pins=["sha256:aa"])
    assert rotated.certificate_pins == frozenset({"sha256:bb"})


def test_retiring_a_signing_key_the_bundle_lacks_is_refused() -> None:
    peer = _peer(signing_key_ids=frozenset({"key-1"}))
    with pytest.raises(FederationRotationError, match="signing key.*does not hold"):
        rotate_bundle(peer, now=0.0, lifetime_seconds=1.0, retire_signing_keys=["key-9"])


def test_retiring_a_certificate_pin_the_bundle_lacks_is_refused() -> None:
    peer = _peer(certificate_pins=frozenset({"sha256:aa"}))
    with pytest.raises(FederationRotationError, match="certificate pin.*does not hold"):
        rotate_bundle(peer, now=0.0, lifetime_seconds=1.0, retire_pins=["sha256:zz"])


def test_a_non_positive_lifetime_is_refused() -> None:
    peer = _peer()
    with pytest.raises(FederationRotationError, match="positive"):
        rotate_bundle(peer, now=0.0, lifetime_seconds=0.0)


def test_retire_is_applied_after_the_union_for_the_same_id() -> None:
    peer = _peer(signing_key_ids=frozenset({"key-1"}))
    rotated, summary = rotate_bundle(
        peer,
        now=0.0,
        lifetime_seconds=1.0,
        add_signing_keys=["key-1"],
        retire_signing_keys=["key-1"],
    )
    assert rotated.signing_key_ids == frozenset()
    assert summary.signing_keys.added == ()
    assert summary.signing_keys.retired == ("key-1",)


def test_rotation_carries_through_the_untouched_policy_fields() -> None:
    grant = ScopeGrant(verb="read_board", namespace="acme/shared")
    peer = _peer(
        domain_id="acme",
        namespaces=frozenset({"acme/shared"}),
        scope_grants=(grant,),
        revoked=False,
    )
    rotated, _ = rotate_bundle(peer, now=0.0, lifetime_seconds=1.0, add_signing_keys=["key-2"])
    assert rotated.domain_id == "acme"
    assert rotated.namespaces == frozenset({"acme/shared"})
    assert rotated.scope_grants == (grant,)
    assert rotated.revoked is False


def test_default_rotation_lifetime_is_a_positive_number_of_days() -> None:
    assert DEFAULT_ROTATION_LIFETIME_DAYS > 0

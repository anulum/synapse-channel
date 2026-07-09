# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation peering lifecycle classification regressions

from __future__ import annotations

from synapse_channel.core.federation import FederationPeer
from synapse_channel.core.federation_lifecycle import (
    SECONDS_PER_DAY,
    classify_federation_lifecycle,
)
from synapse_channel.core.federation_store import FederationRecord, PeerProvenance


def _record(peer: FederationPeer, *, imported_at: float = 0.0) -> FederationRecord:
    return FederationRecord(
        peer=peer,
        provenance=PeerProvenance(
            source="signed-ticket",
            imported_at=imported_at,
            confirmed_by="ops",
        ),
    )


def _peer(**overrides: object) -> FederationPeer:
    base: dict[str, object] = {
        "domain_id": "acme",
        "namespaces": frozenset({"acme/shared"}),
        "certificate_pins": frozenset({"sha256:aa"}),
        "signing_key_ids": frozenset({"key-1"}),
        "expires_at": None,
        "revoked": False,
    }
    base.update(overrides)
    return FederationPeer(**base)  # type: ignore[arg-type]


def test_active_lifecycle_reports_no_expiry_and_steady_material() -> None:
    lifecycle = classify_federation_lifecycle(
        _record(_peer(), imported_at=SECONDS_PER_DAY),
        now=3 * SECONDS_PER_DAY,
        max_age_days=3,
    )

    assert lifecycle.state == "active"
    assert lifecycle.rotation_state == "steady"
    assert lifecycle.expires_label == "never"
    assert lifecycle.expires_in_days is None
    assert lifecycle.expiry_note == "no expiry"
    assert lifecycle.imported_age_days == 2.0
    assert lifecycle.stale is False


def test_stale_is_only_marked_for_active_peerings_over_the_import_age_policy() -> None:
    active = classify_federation_lifecycle(
        _record(_peer(), imported_at=0.0),
        now=5 * SECONDS_PER_DAY,
        max_age_days=3,
    )
    revoked = classify_federation_lifecycle(
        _record(_peer(revoked=True), imported_at=0.0),
        now=5 * SECONDS_PER_DAY,
        max_age_days=3,
    )

    assert active.stale is True
    assert revoked.state == "revoked"
    assert revoked.stale is False


def test_expiry_lifecycle_reports_future_and_past_distances() -> None:
    future = classify_federation_lifecycle(
        _record(_peer(expires_at=12 * SECONDS_PER_DAY)),
        now=10 * SECONDS_PER_DAY,
    )
    expired = classify_federation_lifecycle(
        _record(_peer(expires_at=8 * SECONDS_PER_DAY)),
        now=10 * SECONDS_PER_DAY,
    )

    assert future.state == "active"
    assert future.expires_in_days == 2.0
    assert future.expiry_note == "in 2.0d"
    assert expired.state == "expired"
    assert expired.expires_in_days == -2.0
    assert expired.expiry_note == "expired 2.0d ago"


def test_rotation_state_distinguishes_overlap_from_incomplete_material() -> None:
    overlap = classify_federation_lifecycle(
        _record(_peer(signing_key_ids=frozenset({"key-1", "key-2"}))),
        now=0.0,
    )
    incomplete = classify_federation_lifecycle(
        _record(_peer(certificate_pins=frozenset())),
        now=0.0,
    )

    assert overlap.rotation_state == "overlap"
    assert incomplete.rotation_state == "incomplete"

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation store serialisation and persistence regressions

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.federation import FederationPeer, ScopeGrant
from synapse_channel.core.federation_store import (
    FederationRecord,
    FederationStoreError,
    PeerProvenance,
    bundle_from_store,
    load_store,
    merge_record,
    peer_from_dict,
    peer_to_dict,
    save_store,
)

_PEER = FederationPeer(
    domain_id="acme",
    namespaces=frozenset({"acme/shared"}),
    certificate_pins=frozenset({"sha256:aa"}),
    signing_key_ids=frozenset({"key-1"}),
    scope_grants=(ScopeGrant("read_board", "acme/shared"),),
    expires_at=1200.0,
    revoked=False,
)
_PROV = PeerProvenance(source="signed-bundle", imported_at=100.0, confirmed_by="ops@local")


def test_peer_dict_round_trips() -> None:
    parsed = peer_from_dict(peer_to_dict(_PEER))
    assert parsed == _PEER


def test_peer_from_dict_is_deny_by_default_on_omissions() -> None:
    peer = peer_from_dict({"domain_id": "  bare  "})
    assert peer.domain_id == "bare"
    assert peer.namespaces == frozenset()
    assert peer.certificate_pins == frozenset()
    assert peer.signing_key_ids == frozenset()
    assert peer.scope_grants == ()
    assert peer.expires_at is None and peer.revoked is False


def test_peer_from_dict_rejects_bad_inputs() -> None:
    with pytest.raises(FederationStoreError, match="must be a mapping"):
        peer_from_dict(["not", "a", "mapping"])  # type: ignore[arg-type]
    with pytest.raises(FederationStoreError, match="non-empty domain_id"):
        peer_from_dict({"domain_id": "  "})
    with pytest.raises(FederationStoreError, match="'namespaces' must be a list"):
        peer_from_dict({"domain_id": "a", "namespaces": "nope"})
    with pytest.raises(FederationStoreError, match="'scope_grants' must be a list"):
        peer_from_dict({"domain_id": "a", "scope_grants": "nope"})
    with pytest.raises(FederationStoreError, match="each scope grant must be a mapping"):
        peer_from_dict({"domain_id": "a", "scope_grants": ["bad"]})


@pytest.mark.parametrize(
    "bad_expires",
    [
        "soon",  # non-numeric string -> raw ValueError before the fix
        "1e999x",  # numeric-looking but unparsable
        {},  # mapping -> raw TypeError, which a ValueError-only catch misses
        [1, 2],  # list -> raw TypeError
        float("nan"),  # finite guard: nan defeats the expiry comparison
        float("inf"),  # finite guard: a peering that never expires
    ],
)
def test_peer_from_dict_rejects_non_numeric_expires_at(bad_expires: object) -> None:
    # A hostile or corrupt peer bundle must fail the parser's contract
    # (FederationStoreError), not escape as a raw TypeError/ValueError that the
    # import CLI and the hub-startup path — which catch only FederationStoreError —
    # would let crash with a traceback.
    with pytest.raises(FederationStoreError, match="'expires_at' must be a"):
        peer_from_dict({"domain_id": "evil", "expires_at": bad_expires})


def test_peer_from_dict_accepts_numeric_expires_at_forms() -> None:
    assert peer_from_dict({"domain_id": "a", "expires_at": 1200}).expires_at == 1200.0
    assert peer_from_dict({"domain_id": "a", "expires_at": 1200.5}).expires_at == 1200.5
    assert peer_from_dict({"domain_id": "a", "expires_at": None}).expires_at is None
    assert peer_from_dict({"domain_id": "a"}).expires_at is None


def test_peer_from_dict_skips_incomplete_scope_grants() -> None:
    peer = peer_from_dict(
        {
            "domain_id": "a",
            "scope_grants": [
                {"verb": "read", "namespace": "a/x"},
                {"verb": "", "namespace": "a/x"},  # skipped
                {"verb": "write", "namespace": ""},  # skipped
            ],
        }
    )
    assert peer.scope_grants == (ScopeGrant("read", "a/x"),)


def test_merge_record_adds_and_replaces() -> None:
    a = FederationRecord(_PEER, _PROV)
    records = merge_record({}, a)
    assert list(records) == ["acme"]
    updated = FederationRecord(
        FederationPeer(domain_id="acme", namespaces=frozenset({"acme/new"})), _PROV
    )
    records2 = merge_record(records, updated)
    assert records2["acme"].peer.namespaces == frozenset({"acme/new"})  # replaced
    assert records == {"acme": a}  # original mapping untouched


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "fed" / "store.json"  # parent created on save
    save_store(path, [FederationRecord(_PEER, _PROV)])
    loaded = load_store(path)
    assert loaded["acme"].peer == _PEER
    assert loaded["acme"].provenance == _PROV


def test_load_absent_file_is_empty(tmp_path: Path) -> None:
    assert load_store(tmp_path / "missing.json") == {}


def test_load_rejects_malformed_store(tmp_path: Path) -> None:
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(FederationStoreError, match="not valid JSON"):
        load_store(bad_json)

    no_records = tmp_path / "norec.json"
    no_records.write_text('{"version": 1}', encoding="utf-8")
    with pytest.raises(FederationStoreError, match="'records' list"):
        load_store(no_records)

    bad_prov = tmp_path / "badprov.json"
    bad_prov.write_text(
        '{"version": 1, "records": [{"domain_id": "a", "provenance": "nope"}]}', encoding="utf-8"
    )
    with pytest.raises(FederationStoreError, match="'provenance' must be a mapping"):
        load_store(bad_prov)


@pytest.mark.parametrize("bad_imported_at", ['"xyz"', "{}", "[1]", "NaN"])
def test_load_store_rejects_non_numeric_imported_at(tmp_path: Path, bad_imported_at: str) -> None:
    # provenance.imported_at rides in the same out-of-band store; a malformed value
    # must fail as FederationStoreError (not a raw TypeError/ValueError) so a corrupt
    # store cannot crash `synapse hub --federation-store` at startup.
    store = tmp_path / "badnum.json"
    store.write_text(
        '{"version": 1, "records": [{"domain_id": "a", '
        f'"provenance": {{"imported_at": {bad_imported_at}}}}}]}}',
        encoding="utf-8",
    )
    with pytest.raises(FederationStoreError, match="'provenance.imported_at' must be a"):
        load_store(store)


def test_record_and_provenance_to_dict() -> None:
    record = FederationRecord(_PEER, _PROV)
    payload = record.to_dict()
    assert payload["domain_id"] == "acme"
    assert payload["provenance"] == {
        "source": "signed-bundle",
        "imported_at": 100.0,
        "confirmed_by": "ops@local",
    }


def test_bundle_from_store_is_empty_when_absent(tmp_path: Path) -> None:
    bundle = bundle_from_store(tmp_path / "missing.json")
    assert bundle.domains() == ()


def test_bundle_from_store_builds_policy_over_stored_peers(tmp_path: Path) -> None:
    store = tmp_path / "federation.json"
    revoked = FederationPeer(domain_id="globex", revoked=True)
    save_store(store, [FederationRecord(_PEER, _PROV), FederationRecord(revoked, _PROV)])
    bundle = bundle_from_store(store)
    # every record loads, including the revoked peering (refused at authorisation time)
    assert bundle.domains() == ("acme", "globex")
    allowed = bundle.authorise(
        "acme",
        namespace="acme/shared",
        signing_key_id="key-1",
        certificate_pin="sha256:aa",
        now=1000.0,
    )
    assert allowed.allowed is True
    refused = bundle.authorise(
        "globex",
        namespace="globex/shared",
        signing_key_id="key-1",
        certificate_pin="sha256:aa",
        now=1000.0,
    )
    assert refused.allowed is False

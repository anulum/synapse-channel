# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — operable multi-hub serving-policy configuration tests

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel.core.federation import FederationPeer, ScopeGrant
from synapse_channel.core.federation_store import (
    FederationRecord,
    PeerProvenance,
    save_store,
)
from synapse_channel.core.multihub_serving_config import (
    MultiHubServingConfigError,
    load_multihub_serving_config,
)

_PIN = "sha256:" + ("a" * 64)


def _store(
    path: Path,
    *,
    namespaces: frozenset[str] = frozenset({"PROJECT"}),
    keys: frozenset[str] = frozenset({"key-1"}),
    pins: frozenset[str] = frozenset({_PIN}),
    revoked: bool = False,
) -> Path:
    peer = FederationPeer(
        domain_id="domain-a",
        namespaces=namespaces,
        certificate_pins=pins,
        signing_key_ids=keys,
        scope_grants=(ScopeGrant("observe", "PROJECT"),),
        revoked=revoked,
    )
    save_store(
        path,
        [FederationRecord(peer, PeerProvenance("ceremony", 1.0, "operator"))],
    )
    return path


def _document(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "version": 1,
        "federation_store": "federation.json",
        "client_ca_file": "client-ca.pem",
        "grants": [
            {
                "sender": "fleet-a",
                "domain_id": "domain-a",
                "namespace": "PROJECT",
                "signing_key_id": "key-1",
            }
        ],
    }
    document.update(updates)
    return document


def _policy(tmp_path: Path, document: dict[str, object] | None = None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    _store(tmp_path / "federation.json")
    (tmp_path / "client-ca.pem").write_text("public CA material\n", encoding="utf-8")
    path = tmp_path / "serving-policy.json"
    path.write_text(json.dumps(document or _document()), encoding="utf-8")
    return path


def test_loads_relative_paths_and_composes_one_trust_source(tmp_path: Path) -> None:
    loaded = load_multihub_serving_config(_policy(tmp_path))

    assert loaded.client_ca_file == tmp_path / "client-ca.pem"
    assert loaded.federation_store == tmp_path / "federation.json"
    assert loaded.policy.federation.domains() == ("domain-a",)
    grant = loaded.policy.grants["fleet-a"]
    assert (grant.domain_id, grant.namespace, grant.signing_key_id) == (
        "domain-a",
        "PROJECT",
        "key-1",
    )
    trusted = loaded.policy.mtls.peers["domain-a"]
    assert trusted.certificate_pins == frozenset({_PIN})
    assert trusted.projects == frozenset({"PROJECT"})


def test_preserves_revocation_as_a_live_deny_policy(tmp_path: Path) -> None:
    path = _policy(tmp_path)
    _store(tmp_path / "federation.json", revoked=True)

    loaded = load_multihub_serving_config(path)

    assert loaded.policy.federation.peer("domain-a").revoked is True  # type: ignore[union-attr]
    assert loaded.policy.mtls.peers["domain-a"].revoked is True


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (_document(version=2), "unsupported"),
        (_document(version=True), "version must be an integer"),
        (_document(grants=[]), "non-empty list"),
        (_document(extra=True), "unknown fields"),
        (
            _document(
                grants=[
                    {
                        "sender": "fleet-a",
                        "domain_id": "domain-a",
                        "namespace": "PROJECT",
                        "signing_key_id": "key-1",
                    },
                    {
                        "sender": "fleet-a",
                        "domain_id": "domain-a",
                        "namespace": "PROJECT",
                        "signing_key_id": "key-1",
                    },
                ]
            ),
            "sender 'fleet-a' twice",
        ),
    ],
)
def test_refuses_ambiguous_or_unsupported_documents(
    tmp_path: Path, document: dict[str, object], message: str
) -> None:
    with pytest.raises(MultiHubServingConfigError, match=message):
        load_multihub_serving_config(_policy(tmp_path, document))


@pytest.mark.parametrize(
    ("store_kwargs", "grant_updates", "message"),
    [
        ({}, {"domain_id": "missing"}, "unknown federation domain"),
        ({"namespaces": frozenset({"OTHER"})}, {}, "namespace is absent"),
        ({"keys": frozenset({"other-key"})}, {}, "signing key is absent"),
        ({"pins": frozenset()}, {}, "has no certificate pin"),
    ],
)
def test_refuses_grants_not_backed_by_federation_material(
    tmp_path: Path,
    store_kwargs: dict[str, object],
    grant_updates: dict[str, object],
    message: str,
) -> None:
    path = _policy(tmp_path)
    _store(tmp_path / "federation.json", **store_kwargs)  # type: ignore[arg-type]
    document = _document()
    grant = dict(document["grants"][0])  # type: ignore[index]
    grant.update(grant_updates)
    document["grants"] = [grant]
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(MultiHubServingConfigError, match=message):
        load_multihub_serving_config(path)


def test_refuses_duplicate_json_fields_and_symlinked_policy(tmp_path: Path) -> None:
    path = _policy(tmp_path)
    path.write_text(
        '{"version":1,"version":1,"federation_store":"federation.json",'
        '"client_ca_file":"client-ca.pem","grants":[]}',
        encoding="utf-8",
    )
    with pytest.raises(MultiHubServingConfigError, match="repeats field 'version'"):
        load_multihub_serving_config(path)

    target = _policy(tmp_path / "other")
    alias = tmp_path / "policy-link.json"
    alias.symlink_to(target)
    with pytest.raises(MultiHubServingConfigError, match="cannot securely open"):
        load_multihub_serving_config(alias)


def test_refuses_missing_client_ca_before_hub_start(tmp_path: Path) -> None:
    path = _policy(tmp_path)
    (tmp_path / "client-ca.pem").unlink()

    with pytest.raises(MultiHubServingConfigError, match="multi-hub client CA"):
        load_multihub_serving_config(path)

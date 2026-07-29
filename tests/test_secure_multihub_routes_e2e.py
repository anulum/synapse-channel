# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real mutual-TLS matrix across every multi-hub transport
"""Exercise all multi-hub transports through one real client-CA WSS boundary."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import ipaddress
import json
import sqlite3
import ssl
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from websockets.asyncio.client import connect

from hub_e2e_helpers import _await_listening, _free_port, read_until_type, send_json
from synapse_channel.core.dead_letter_forwarding import forwarding_notice
from synapse_channel.core.dead_letter_forwarding_transport import forward_dead_letter
from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_claim_transport import forward_claim
from synapse_channel.core.multihub_claim_wire import ClaimForwardRequest
from synapse_channel.core.multihub_equivocation import MultiHubEquivocationError
from synapse_channel.core.multihub_follower import MultiHubFollower
from synapse_channel.core.multihub_serving import MultiHubServingGrant, MultiHubServingPolicy
from synapse_channel.core.multihub_transport import (
    MultiHubFetchError,
    network_fetcher,
    pinned_connector,
)
from synapse_channel.core.multihub_watch import MultiHubWatch
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_transport import relay_operator_action
from synapse_channel.core.operator_relay_wire import RelayActionRequest
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.tls import (
    MTLSPeerTrustBundle,
    MTLSTrustedPeer,
    build_server_ssl_context,
    certificate_sha256_pin,
)

_NAMESPACE = "SYNAPSE-CHANNEL"
_OWNER = "syn-owner"
_ORIGIN = "syn-edge"
_DOMAIN = "edge.example"
_KEY_ID = "SYNAPSE-CHANNEL:hub:2026-07"


@dataclass(frozen=True, slots=True)
class _Identity:
    cert: Path
    key: Path


@dataclass(frozen=True, slots=True)
class _TLSMaterial:
    ca: Path
    server: _Identity
    authorised: _Identity
    wrong_pin: _Identity
    untrusted_ca: _Identity
    server_pin: str
    authorised_pin: str


def _certificate_authority(common_name: str) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = dt.datetime.now(dt.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def _issue_identity(
    root: Path,
    name: str,
    *,
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    server: bool = False,
) -> _Identity:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
    )
    if server:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
    certificate = builder.sign(ca_key, hashes.SHA256())
    cert_path = root / f"{name}.pem"
    key_path = root / f"{name}.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.chmod(0o600)
    key_path.chmod(0o600)
    return _Identity(cert=cert_path, key=key_path)


def _tls_material(tmp_path: Path) -> _TLSMaterial:
    ca_key, ca_cert = _certificate_authority("synapse-multihub-test-ca")
    ca_path = tmp_path / "client-ca.pem"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    server = _issue_identity(tmp_path, "server", ca_key=ca_key, ca_cert=ca_cert, server=True)
    authorised = _issue_identity(tmp_path, "authorised", ca_key=ca_key, ca_cert=ca_cert)
    wrong_pin = _issue_identity(tmp_path, "wrong-pin", ca_key=ca_key, ca_cert=ca_cert)
    rogue_key, rogue_ca = _certificate_authority("untrusted-test-ca")
    untrusted_ca = _issue_identity(tmp_path, "untrusted", ca_key=rogue_key, ca_cert=rogue_ca)
    return _TLSMaterial(
        ca=ca_path,
        server=server,
        authorised=authorised,
        wrong_pin=wrong_pin,
        untrusted_ca=untrusted_ca,
        server_pin=certificate_sha256_pin(server.cert),
        authorised_pin=certificate_sha256_pin(authorised.cert),
    )


def _serving_policy(material: _TLSMaterial, *, revoked: bool = False) -> MultiHubServingPolicy:
    peer = FederationPeer(
        domain_id=_DOMAIN,
        namespaces=frozenset({_NAMESPACE}),
        certificate_pins=frozenset({material.authorised_pin}),
        signing_key_ids=frozenset({_KEY_ID}),
        scope_grants=tuple(
            ScopeGrant(verb=verb, namespace=_NAMESPACE) for verb in ("read", "claim", "release")
        ),
        revoked=revoked,
    )
    return MultiHubServingPolicy(
        federation=FederationBundle([peer]),
        mtls=MTLSPeerTrustBundle(
            peers={
                _DOMAIN: MTLSTrustedPeer(
                    peer_id=_DOMAIN,
                    certificate_pins=frozenset({material.authorised_pin}),
                    signing_key_ids=frozenset({_KEY_ID}),
                    projects=frozenset({_NAMESPACE}),
                    revoked=revoked,
                )
            }
        ),
        grants={
            _ORIGIN: MultiHubServingGrant(
                domain_id=_DOMAIN,
                namespace=_NAMESPACE,
                signing_key_id=_KEY_ID,
            )
        },
        clock=time.time,
    )


def _secure_connector(material: _TLSMaterial, identity: _Identity | None) -> Any:
    """Return the one production connector accepted by all transport protocols."""
    return cast(
        Any,
        pinned_connector(
            material.server_pin,
            client_certificate_file=identity.cert if identity else None,
            client_key_file=identity.key if identity else None,
        ),
    )


@contextlib.asynccontextmanager
async def _running_secure_hub(
    tmp_path: Path,
) -> AsyncIterator[tuple[SynapseHub, EventStore, _TLSMaterial, str]]:
    material = _tls_material(tmp_path)
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(
        hub_id=_OWNER,
        journal=store,
        namespace_ownership=NamespaceOwnership(owners={_NAMESPACE: _OWNER}, local_hub_id=_OWNER),
        multihub_serving_policy=_serving_policy(material),
    )
    context = build_server_ssl_context(
        certfile=material.server.cert,
        keyfile=material.server.key,
        client_ca_file=material.ca,
    )
    port = _free_port()
    task = asyncio.create_task(hub.serve("localhost", port, ssl_context=context))
    try:
        await _await_listening(port)
        yield hub, store, material, f"wss://localhost:{port}"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        store.close()


async def _seed_chat(uri: str, ca_file: Path) -> None:
    context = ssl.create_default_context(cafile=str(ca_file))
    async with connect(uri, ssl=context) as websocket:
        await read_until_type(websocket, "welcome")
        await send_json(websocket, sender="local-writer", type="heartbeat")
        await send_json(websocket, sender="local-writer", type="chat", payload="seed")
        await read_until_type(websocket, "chat")


async def _wait_for_event_count(store: EventStore, kind: str, count: int) -> None:
    async def _wait() -> None:
        while sum(event.kind == kind for event in store.read_all()) < count:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait(), timeout=2.0)


async def test_follower_and_watch_share_the_live_mtls_allow_deny_boundary(
    tmp_path: Path,
) -> None:
    async with _running_secure_hub(tmp_path) as (hub, _store, material, uri):
        await _seed_chat(uri, material.ca)
        allowed = network_fetcher(
            uri,
            local_id=_ORIGIN,
            connector=_secure_connector(material, material.authorised),
        )
        assert len(await allowed(0)) == 1

        for identity in (None, material.wrong_pin):
            denied = network_fetcher(
                uri,
                local_id=_ORIGIN,
                connector=_secure_connector(material, identity),
            )
            assert await denied(0) == ()

        with pytest.raises(MultiHubFetchError):
            await network_fetcher(
                uri,
                local_id=_ORIGIN,
                connector=_secure_connector(material, material.untrusted_ca),
            )(0)

        follower = MultiHubFollower()
        watch = MultiHubWatch(
            {_OWNER: uri},
            local_id=_ORIGIN,
            follower=follower,
            pins={_OWNER: material.server_pin},
            client_certificate_file=str(material.authorised.cert),
            client_key_file=str(material.authorised.key),
        )
        assert await watch.poll_once() == {_OWNER: None}
        assert follower.cursor(_OWNER) == 1

        denied_follower = MultiHubFollower()
        denied_watch = MultiHubWatch(
            {_OWNER: uri},
            local_id=_ORIGIN,
            follower=denied_follower,
            pins={_OWNER: material.server_pin},
            client_certificate_file=str(material.wrong_pin.cert),
            client_key_file=str(material.wrong_pin.key),
        )
        assert await denied_watch.poll_once() == {_OWNER: None}
        assert denied_follower.cursor(_OWNER) == 0

        hub.multihub_serving_policy = _serving_policy(material, revoked=True)
        assert await allowed(0) == ()


async def test_authenticated_mtls_peer_equivocation_still_fails_closed(
    tmp_path: Path,
) -> None:
    async with _running_secure_hub(tmp_path) as (_hub, _store, material, uri):
        await _seed_chat(uri, material.ca)
        authenticated = network_fetcher(
            uri,
            local_id=_ORIGIN,
            connector=_secure_connector(material, material.authorised),
        )
        evidence = EventStore(tmp_path / "observer.db")
        follower = MultiHubFollower(journal=evidence, observer_id=_ORIGIN, clock=lambda: 70.0)
        await follower.poll(_OWNER, authenticated)
        before = follower.observed().to_dict()

        # Model a compromised or rolled-back authenticated peer by rewriting its
        # temporary test journal, then pull the hostile old identity through the
        # same real mTLS serving path. Production code performs no such mutation.
        with sqlite3.connect(tmp_path / "events.db") as connection:
            connection.execute(
                "UPDATE events SET payload = ? WHERE seq = 1",
                (json.dumps({"sender": "local-writer", "payload": "equivocated"}),),
            )

        async def hostile_replay(_after_seq: int) -> Sequence[StoredEvent]:
            return await authenticated(0)

        with pytest.raises(MultiHubEquivocationError):
            await follower.poll(_OWNER, hostile_replay)

        assert follower.cursor(_OWNER) == 1
        assert follower.observed().to_dict() == before
        assert [event.kind for event in evidence.read_all()] == [EventKind.MULTIHUB_EQUIVOCATION]
        evidence.close()


async def test_claim_and_operator_relay_apply_only_for_the_authorised_live_identity(
    tmp_path: Path,
) -> None:
    async with _running_secure_hub(tmp_path) as (hub, _store, material, uri):
        allowed_connector = _secure_connector(material, material.authorised)
        denied_connector = _secure_connector(material, material.wrong_pin)
        request = ClaimForwardRequest(
            namespace=_NAMESPACE,
            claimant=f"{_NAMESPACE}/worker",
            task_id="secure-route",
            claim={"task_id": "secure-route", "note": "secure route proof"},
        )
        granted = await forward_claim(
            request,
            uri=uri,
            local_id=_ORIGIN,
            connector=allowed_connector,
        )
        assert granted.granted is True
        assert "secure-route" in hub.state.claims

        denied = await forward_claim(
            ClaimForwardRequest(
                namespace=_NAMESPACE,
                claimant=f"{_NAMESPACE}/other",
                task_id="denied-route",
                claim={"task_id": "denied-route"},
            ),
            uri=uri,
            local_id=_ORIGIN,
            connector=denied_connector,
        )
        assert denied.granted is False
        assert "denied-route" not in hub.state.claims

        released = await relay_operator_action(
            RelayActionRequest(
                action="release",
                namespace=_NAMESPACE,
                task_id="secure-route",
                operator="operator-a",
                origin_hub_id=_ORIGIN,
                reason="real mTLS route proof",
            ),
            uri=uri,
            local_id=_ORIGIN,
            connector=allowed_connector,
        )
        assert released.applied is True
        assert "secure-route" not in hub.state.claims

        refused = await relay_operator_action(
            RelayActionRequest(
                action="release",
                namespace=_NAMESPACE,
                task_id="not-present",
                operator="operator-b",
                origin_hub_id=_ORIGIN,
            ),
            uri=uri,
            local_id=_ORIGIN,
            connector=denied_connector,
        )
        assert refused.applied is False
        assert refused.detail == "peer_not_authorised"

        hub.multihub_serving_policy = _serving_policy(material, revoked=True)
        revoked = await forward_claim(
            ClaimForwardRequest(
                namespace=_NAMESPACE,
                claimant=f"{_NAMESPACE}/revoked",
                task_id="revoked-route",
                claim={"task_id": "revoked-route"},
            ),
            uri=uri,
            local_id=_ORIGIN,
            connector=allowed_connector,
        )
        assert revoked.granted is False
        assert "revoked-route" not in hub.state.claims


async def test_dead_letter_pointer_is_persisted_only_for_the_authorised_identity(
    tmp_path: Path,
) -> None:
    async with _running_secure_hub(tmp_path) as (hub, store, material, uri):
        notice = forwarding_notice(
            f"{_NAMESPACE}/reader",
            2,
            origin_hub_id=_ORIGIN,
            owner_hub_id=_OWNER,
        )
        await forward_dead_letter(
            notice,
            uri=uri,
            local_id=_ORIGIN,
            connector=_secure_connector(material, material.authorised),
        )
        await _wait_for_event_count(store, EventKind.DEAD_LETTER_FORWARDING, 1)

        for identity in (None, material.wrong_pin):
            await forward_dead_letter(
                notice,
                uri=uri,
                local_id=_ORIGIN,
                connector=_secure_connector(material, identity),
            )
        await asyncio.sleep(0.05)
        assert (
            sum(event.kind == EventKind.DEAD_LETTER_FORWARDING for event in store.read_all()) == 1
        )

        hub.multihub_serving_policy = _serving_policy(material, revoked=True)
        await forward_dead_letter(
            notice,
            uri=uri,
            local_id=_ORIGIN,
            connector=_secure_connector(material, material.authorised),
        )
        await asyncio.sleep(0.05)
        assert (
            sum(event.kind == EventKind.DEAD_LETTER_FORWARDING for event in store.read_all()) == 1
        )

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real recipient-granted attachment sockets and owner revocation
"""Cross the real hub boundary with verified registrations and private content."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import ssl
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import connect

from hub_e2e_helpers import _await_listening, _free_port, read_until_type, running_hub
from multihub_tls_helpers import certificate_authority, issue_identity
from synapse_channel.core.acl import ATTACHMENT_READ, AclPolicy, AclRule
from synapse_channel.core.attachment_serving import AttachmentServingPolicy
from synapse_channel.core.attachment_store import MAX_CHUNK_BYTES, AttachmentError, AttachmentStore
from synapse_channel.core.attachment_transport import request_attachment
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageAuthKey,
    MessageReplayCache,
)
from synapse_channel.core.message_auth_durable import DurableMessageAuthReplayStore
from synapse_channel.core.multihub_serving import MultiHubServingGrant, MultiHubServingPolicy
from synapse_channel.core.peer_identity import PeerRegistrationSigner, signed
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType, build_envelope
from synapse_channel.core.role_grants import RoleGrants
from synapse_channel.core.tls import (
    MTLSPeerTrustBundle,
    MTLSTrustedPeer,
    build_server_ssl_context,
    certificate_sha256_pin,
)

pytestmark = pytest.mark.real_hub

RECIPIENT = "hub-recipient"
SOURCE = "hub-source"
SCOPE = "PROJECT"
BODY = b"private attachment bytes\x00" * 2000
DIGEST = hashlib.sha256(BODY).hexdigest()


@dataclass
class Source:
    """One fully governed production hub and the owner's fixture objects."""

    hub: SynapseHub
    store: AttachmentStore
    policy_path: Path
    signer: PeerRegistrationSigner
    make_hub: Callable[[AttachmentStore], SynapseHub]

    def write_grants(self, grants: list[dict[str, object]]) -> None:
        """Replace permissions atomically like the source operator."""
        replacement = self.policy_path.with_suffix(".new")
        replacement.write_text(json.dumps({"version": 1, "grants": grants}), encoding="utf-8")
        replacement.chmod(0o600)
        replacement.replace(self.policy_path)

    def grant(self, **changes: object) -> dict[str, object]:
        """Return an exact owner-granted object permission."""
        return {
            "recipient_hub": RECIPIENT,
            "scope": SCOPE,
            "digest": DIGEST,
            "expires_at": time.time() + 3600,
            **changes,
        }


@pytest.fixture
def source(tmp_path: Path) -> Iterator[Source]:
    identity = Ed25519PrivateKey.generate()
    senders = frozenset({RECIPIENT, "PROJECT/alice"})
    trust = EventSignatureTrustBundle(
        keys={
            "identity": EventSignatureKey.from_private_key(
                key_id="identity",
                private_key=identity,
                senders=senders,
            )
        },
        replay_cache=MessageReplayCache(window_seconds=30, max_entries=512),
    )
    peer = FederationPeer(
        domain_id="recipient-domain",
        namespaces=frozenset({SCOPE}),
        signing_key_ids=frozenset({"peer-key"}),
        scope_grants=(ScopeGrant("read", SCOPE),),
    )
    serving = MultiHubServingPolicy(
        federation=FederationBundle([peer]),
        mtls=MTLSPeerTrustBundle(peers={}),
        grants={
            RECIPIENT: MultiHubServingGrant(
                "recipient-domain",
                SCOPE,
                "peer-key",
                identity_key_id="identity",
            )
        },
        clock=time.time,
    )
    store = AttachmentStore(tmp_path / "objects")
    token = store.begin(
        scope=SCOPE,
        sender="PROJECT/alice",
        digest=DIGEST,
        length=len(BODY),
        media_type="application/octet-stream",
        provenance="claim:peer-test",
        expires_at=time.time() + 3600,
    )
    for offset in range(0, len(BODY), MAX_CHUNK_BYTES):
        store.chunk(token, "PROJECT/alice", offset, BODY[offset : offset + MAX_CHUNK_BYTES])
    store.commit(token, "PROJECT/alice")
    store.reference(SCOPE, DIGEST, "claim:retained")
    policy_path = tmp_path / "recipients.json"
    policy_path.write_text('{"version":1,"grants":[]}', encoding="utf-8")
    policy_path.chmod(0o600)
    journal = EventStore(tmp_path / "events.db")
    replay = DurableMessageAuthReplayStore(
        tmp_path / "replay.db", max_entries=512, window_seconds=30
    )

    def make_hub(attachment_store: AttachmentStore) -> SynapseHub:
        return SynapseHub(
            hub_id=SOURCE,
            attachment_store=attachment_store,
            journal=journal,
            attachment_serving_policy=AttachmentServingPolicy(policy_path),
            multihub_serving_policy=serving,
            authenticator=TokenAuthenticator({"test-token": set(senders)}),
            identity_trust_bundle=trust,
            require_identity_binding=True,
            per_message_auth_keys={"hmac": MessageAuthKey("hmac", b"test-peer-key", senders)},
            require_per_message_auth=True,
            per_message_auth_replay_store=replay,
            require_acl=True,
            acl_policy=AclPolicy([AclRule(ATTACHMENT_READ, "attachment", "*", namespace=SCOPE)]),
            role_grants=RoleGrants({f"{SCOPE}/{ATTACHMENT_READ}": frozenset({"PROJECT/alice"})}),
        )

    fixture = Source(
        make_hub(store), store, policy_path, PeerRegistrationSigner(identity, "identity"), make_hub
    )
    fixture.write_grants([fixture.grant()])
    try:
        yield fixture
    finally:
        replay.close()
        journal.close()
        fixture.store.close()


async def _ask(source: Source, uri: str, action: str = "info", **fields: Any) -> dict[str, Any]:
    return await request_attachment(
        action,
        uri=uri,
        local_id=RECIPIENT,
        source_hub_id=SOURCE,
        scope=fields.pop("scope", SCOPE),
        digest=fields.pop("digest", DIGEST),
        signer=fields.pop("signer", source.signer),
        token="test-token",
        timeout=3,
        **fields,
    )


async def test_real_peer_reads_exact_bytes_without_mutating_source_references(
    source: Source,
) -> None:
    async with running_hub(source.hub) as (_, uri):
        metadata = await _ask(source, uri)
        first = await _ask(source, uri, "read", offset=0)
        second = await _ask(source, uri, "read", offset=len(first["body"]))
        eof = await _ask(source, uri, "read", offset=len(BODY))
    assert metadata["digest"] == DIGEST and metadata["length"] == len(BODY)
    assert first["body"] + second["body"] == BODY
    assert first["eof"] is False and second["eof"] is True
    assert eof["body"] == b"" and eof["eof"] is True
    assert source.store.gc(SCOPE) == []


async def test_revoke_and_restore_grant_between_chunks(source: Source) -> None:
    async with running_hub(source.hub) as (_, uri):
        first = await _ask(source, uri, "read", offset=0)
        source.write_grants([])
        with pytest.raises(AttachmentError, match="attachment unavailable"):
            await _ask(source, uri, "read", offset=len(first["body"]))
        source.write_grants([source.grant()])
        second = await _ask(source, uri, "read", offset=len(first["body"]))
        source.write_grants([source.grant(expires_at=time.time() - 1)])
        with pytest.raises(AttachmentError, match="attachment unavailable"):
            await _ask(source, uri)
        source.policy_path.write_text("{malformed", encoding="utf-8")
        with pytest.raises(AttachmentError, match="attachment unavailable"):
            await _ask(source, uri)
    assert first["body"] + second["body"] == BODY


async def test_namespace_grant_alone_never_authorises_content(source: Source) -> None:
    async with running_hub(source.hub) as (_, uri):
        for changes in ({"recipient_hub": "other-hub"}, {"digest": "b" * 64}, {"scope": "OTHER"}):
            source.write_grants([source.grant(**changes)])
            with pytest.raises(AttachmentError, match="attachment unavailable"):
                await _ask(source, uri)
        source.write_grants([source.grant()])
        for fields in ({"digest": "b" * 64}, {"scope": "OTHER"}):
            with pytest.raises(AttachmentError, match="attachment unavailable"):
                await _ask(source, uri, **fields)
        policy = source.hub.multihub_serving_policy
        assert policy is not None
        source.hub.multihub_serving_policy = replace(policy, grants={})
        with pytest.raises(AttachmentError, match="attachment unavailable"):
            await _ask(source, uri)


@pytest.mark.parametrize(
    "fields",
    [
        {"action": "write"},
        {"scope": None},
        {"digest": []},
        {"offset": True},
        {"offset": -1},
        {"offset": len(BODY) + 1},
        {"offset": "0"},
        {"offset": {}},
    ],
)
async def test_malformed_peer_documents_have_one_fixed_private_refusal(
    source: Source, fields: dict[str, object]
) -> None:
    async with running_hub(source.hub) as (_, uri):
        async with connect(uri) as socket:
            registration = signed(
                build_envelope(
                    RECIPIENT,
                    MessageType.HEARTBEAT,
                    token="test-token",
                    protocol_version=6,
                ),
                source.signer,
            )
            await socket.send(json.dumps(registration))
            await read_until_type(socket, MessageType.WELCOME)
            frame = build_envelope(
                RECIPIENT,
                MessageType.ATTACHMENT_PEER_REQUEST,
                action="read",
                scope=SCOPE,
                digest=DIGEST,
                offset=0,
            )
            frame.update(fields)
            await socket.send(json.dumps(frame))
            response = await read_until_type(socket, MessageType.ATTACHMENT_PEER_RESULT)
    assert response["ok"] is False and response["error"] == "attachment unavailable"
    assert "body" not in response and "metadata" not in response


async def test_existing_socket_rechecks_grants(source: Source) -> None:
    async with running_hub(source.hub) as (_, uri):
        async with connect(uri) as socket:
            await socket.send(
                json.dumps(
                    signed(
                        build_envelope(
                            RECIPIENT,
                            MessageType.HEARTBEAT,
                            token="test-token",
                            protocol_version=6,
                        ),
                        source.signer,
                    )
                )
            )
            await read_until_type(socket, MessageType.WELCOME)
            frame = build_envelope(
                RECIPIENT,
                MessageType.ATTACHMENT_PEER_REQUEST,
                action="info",
                scope=SCOPE,
                digest=DIGEST,
            )
            await socket.send(json.dumps(frame))
            assert (await read_until_type(socket, MessageType.ATTACHMENT_PEER_RESULT))["ok"] is True
            source.write_grants([])
            await socket.send(json.dumps(frame))
            assert (await read_until_type(socket, MessageType.ATTACHMENT_PEER_RESULT))[
                "ok"
            ] is False


async def test_restart_keeps_content_and_owner_policy(source: Source) -> None:
    async with running_hub(source.hub) as (_, uri):
        first = await _ask(source, uri, "read", offset=0)
    root = source.store.root
    source.store.close()
    source.store = AttachmentStore(root)
    source.hub = source.make_hub(source.store)
    async with running_hub(source.hub) as (_, uri):
        second = await _ask(source, uri, "read", offset=len(first["body"]))
        source.write_grants([])
        with pytest.raises(AttachmentError, match="attachment unavailable"):
            await _ask(source, uri)
    assert first["body"] + second["body"] == BODY
    assert source.store.gc(SCOPE) == []


async def test_expired_content_and_missing_objects_share_the_same_refusal(source: Source) -> None:
    body = b"expires before peer read"
    digest = hashlib.sha256(body).hexdigest()
    token = source.store.begin(
        scope=SCOPE,
        sender="PROJECT/alice",
        digest=digest,
        length=len(body),
        media_type="text/plain",
        provenance="claim:expired",
        expires_at=time.time() + 0.1,
    )
    source.store.chunk(token, "PROJECT/alice", 0, body)
    source.store.commit(token, "PROJECT/alice")
    source.store.reference(SCOPE, digest, "claim:expired")
    source.write_grants([source.grant(digest=digest), source.grant(digest="b" * 64)])
    await asyncio.sleep(0.12)
    async with running_hub(source.hub) as (_, uri):
        for object_digest in (digest, "b" * 64):
            with pytest.raises(AttachmentError, match="attachment unavailable"):
                await _ask(source, uri, digest=object_digest)
    assert source.store.gc(SCOPE) == []


@pytest.mark.parametrize("version", [None, 4, 5])
async def test_old_registered_peer_gets_uniform_refusal(
    source: Source, version: int | None
) -> None:
    async with running_hub(source.hub) as (_, uri):
        async with connect(uri) as socket:
            await socket.send(
                json.dumps(
                    signed(
                        build_envelope(
                            RECIPIENT,
                            MessageType.HEARTBEAT,
                            token="test-token",
                            protocol_version=version,
                        ),
                        source.signer,
                    )
                )
            )
            await read_until_type(socket, MessageType.WELCOME)
            await socket.send(
                json.dumps(
                    build_envelope(
                        RECIPIENT,
                        MessageType.ATTACHMENT_PEER_REQUEST,
                        action="info",
                        scope=SCOPE,
                        digest=DIGEST,
                    )
                )
            )
            response = await read_until_type(socket, MessageType.ATTACHMENT_PEER_RESULT)
    assert response["ok"] is False and response["error"] == "attachment unavailable"


async def test_disabled_feature_refuses_even_on_a_version_six_hub() -> None:
    async with running_hub(SynapseHub(hub_id=SOURCE)) as (_, uri):
        with pytest.raises(AttachmentError, match="attachment unavailable"):
            await request_attachment(
                "info",
                uri=uri,
                local_id=RECIPIENT,
                source_hub_id=SOURCE,
                scope=SCOPE,
                digest=DIGEST,
            )


async def test_real_mtls_certificate_binds_recipient_and_checks_revocation(
    source: Source, tmp_path: Path
) -> None:
    ca_key, ca_cert = certificate_authority("attachment-peer-ca")
    ca = tmp_path / "ca.pem"
    ca.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    ca.chmod(0o600)
    server = issue_identity(tmp_path, "server", ca_key=ca_key, ca_cert=ca_cert, server=True)
    recipient = issue_identity(tmp_path, "recipient", ca_key=ca_key, ca_cert=ca_cert)
    wrong = issue_identity(tmp_path, "wrong", ca_key=ca_key, ca_cert=ca_cert)
    pin = certificate_sha256_pin(recipient.cert)
    domain = "recipient-domain"
    source.hub.multihub_serving_policy = MultiHubServingPolicy(
        federation=FederationBundle(
            [
                FederationPeer(
                    domain_id=domain,
                    namespaces=frozenset({SCOPE}),
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({"peer-key"}),
                    scope_grants=(ScopeGrant("read", SCOPE),),
                )
            ]
        ),
        mtls=MTLSPeerTrustBundle(
            peers={
                domain: MTLSTrustedPeer(
                    domain,
                    frozenset({pin}),
                    frozenset({"peer-key"}),
                    frozenset({SCOPE}),
                )
            }
        ),
        grants={RECIPIENT: MultiHubServingGrant(domain, SCOPE, "peer-key")},
        clock=time.time,
    )
    context = build_server_ssl_context(certfile=server.cert, keyfile=server.key, client_ca_file=ca)
    port = _free_port()
    task = asyncio.create_task(source.hub.serve("localhost", port, ssl_context=context))
    try:
        await _await_listening(port)
        uri = f"wss://localhost:{port}"
        for certificate, allowed in ((recipient, True), (wrong, False)):
            client = ssl.create_default_context(cafile=str(ca))
            client.load_cert_chain(certificate.cert, certificate.key)
            if allowed:
                info = await _ask(source, uri, signer=None, ssl_context=client)
                assert info["length"] == len(BODY)
                source.write_grants([])
                with pytest.raises(AttachmentError, match="attachment unavailable"):
                    await _ask(source, uri, signer=None, ssl_context=client)
                source.write_grants([source.grant()])
            else:
                with pytest.raises(AttachmentError):
                    await _ask(source, uri, signer=None, ssl_context=client)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_an_active_peering_without_read_scope_cannot_serve_content(source: Source) -> None:
    policy = source.hub.multihub_serving_policy
    assert policy is not None
    source.hub.multihub_serving_policy = replace(
        policy,
        federation=FederationBundle(
            [
                FederationPeer(
                    domain_id="recipient-domain",
                    namespaces=frozenset({SCOPE}),
                    signing_key_ids=frozenset({"peer-key"}),
                    scope_grants=(),
                ),
            ]
        ),
    )
    async with running_hub(source.hub) as (_, uri):
        with pytest.raises(AttachmentError, match="attachment unavailable"):
            await _ask(source, uri)


@pytest.mark.parametrize("revoked", [True, False])
async def test_revoked_or_expired_peering_stops_source_reads(source: Source, revoked: bool) -> None:
    policy = source.hub.multihub_serving_policy
    assert policy is not None
    source.hub.multihub_serving_policy = replace(
        policy,
        federation=FederationBundle(
            [
                FederationPeer(
                    domain_id="recipient-domain",
                    namespaces=frozenset({SCOPE}),
                    signing_key_ids=frozenset({"peer-key"}),
                    scope_grants=(ScopeGrant("read", SCOPE),),
                    revoked=revoked,
                    expires_at=None if revoked else time.time() - 1,
                ),
            ]
        ),
    )
    async with running_hub(source.hub) as (_, uri):
        with pytest.raises(AttachmentError, match="attachment unavailable"):
            await _ask(source, uri)

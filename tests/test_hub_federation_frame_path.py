# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the federation gate on the live agent-frame authorisation path
"""Tests for the hub's federation gate over cross-domain agent frames.

The classification branches (local versus cross-domain) and the deny-reason
branches are exercised by calling :meth:`SynapseHub._authorise_federation`
directly with constructed frames, which keeps the per-message-auth ordering out
of the way; the wiring into ``_handle_message`` (allow routes the frame, deny
returns an error, a local frame is unaffected) is proved end-to-end over a real
socket. The end-to-end harness runs on plaintext ``ws://``, so the live peer
certificate is injected through ``federation_cert_source`` — the same technique
the multi-hub serving tests use — to exercise the decision without a mutual-TLS
handshake.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID
from websockets.asyncio.client import connect

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.acl import CLAIM, MESSAGE
from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant
from synapse_channel.core.hub import FrameDisposition, SynapseHub
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
    sign_event_frame,
)
from synapse_channel.core.protocol import MessageType, build_envelope
from synapse_channel.core.tls import certificate_sha256_pin_from_der

_DOMAIN = "domain-b"
_KEY_ID = "domain-b:main:2026-06"
_LOCAL_KEY_ID = "SYNAPSE-CHANNEL:main:2026-06"
_NAMESPACE = "SYNAPSE-CHANNEL"
_REMOTE = "SYNAPSE-CHANNEL/remote"


def _peer_der() -> bytes:
    """Return the DER bytes of a fresh self-signed certificate the gate can pin."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "peer-fed")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2020, 1, 1, tzinfo=timezone.utc))
        .not_valid_after(datetime(2030, 1, 1, tzinfo=timezone.utc))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.DER)


_DER = _peer_der()
_PIN = certificate_sha256_pin_from_der(_DER)


def _peer(*, scope: tuple[ScopeGrant, ...]) -> FederationPeer:
    return FederationPeer(
        domain_id=_DOMAIN,
        namespaces=frozenset({_NAMESPACE}),
        certificate_pins=frozenset({_PIN}),
        signing_key_ids=frozenset({_KEY_ID}),
        scope_grants=scope,
    )


def _trust_bundle() -> EventSignatureTrustBundle:
    """A non-empty trust bundle; the gate only checks that one is configured.

    The direct-call tests reach :meth:`SynapseHub._authorise_federation` without
    running :meth:`_verify_per_message_auth`, so no signature is verified here — the
    gate only reads that a bundle is present when deciding ``signature_ok``.
    """
    remote = EventSignatureKey.from_private_key(
        key_id=_KEY_ID,
        private_key=Ed25519PrivateKey.generate(),
        senders=frozenset({_REMOTE}),
        projects=frozenset({_NAMESPACE}),
    )
    return EventSignatureTrustBundle(
        keys={_KEY_ID: remote},
        replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
    )


def _hub(
    *,
    scope: tuple[ScopeGrant, ...] = (ScopeGrant(CLAIM, _NAMESPACE),),
    require_auth: bool = True,
    bundle: bool = True,
    cert: bytes | None = _DER,
) -> SynapseHub:
    return SynapseHub(
        hub_id="syn-a",
        require_per_message_auth=require_auth,
        signed_event_trust_bundle=_trust_bundle(),
        federation_bundle=FederationBundle([_peer(scope=scope)]) if bundle else None,
        federation_cert_source=lambda _websocket: cert,
    )


class _Recorder:
    """A minimal socket capturing the frames the gate sends on a deny."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


def _claim(*, key_id: str | None = _KEY_ID, sender: str = _REMOTE) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": MessageType.CLAIM, "sender": sender, "task_id": "T1"}
    if key_id is not None:
        frame["signature"] = {"key_id": key_id}
    return frame


# --- classification: a frame that is not a peered cross-domain frame stays local ---


async def test_no_bundle_leaves_every_frame_local() -> None:
    disposition = await _hub(bundle=False)._authorise_federation(
        _REMOTE, MessageType.CLAIM, _claim(), _Recorder()
    )
    assert disposition is FrameDisposition.LOCAL


async def test_unsigned_frame_is_local() -> None:
    # no signature block -> no key id -> a local frame, gate is a no-op
    disposition = await _hub()._authorise_federation(
        _REMOTE, MessageType.CHAT, {"type": MessageType.CHAT, "sender": _REMOTE}, _Recorder()
    )
    assert disposition is FrameDisposition.LOCAL


async def test_blank_key_id_is_local() -> None:
    disposition = await _hub()._authorise_federation(
        _REMOTE, MessageType.CLAIM, _claim(key_id=""), _Recorder()
    )
    assert disposition is FrameDisposition.LOCAL


async def test_no_live_certificate_is_local() -> None:
    # a plaintext connection presents no certificate -> cannot be cross-domain
    disposition = await _hub(cert=None)._authorise_federation(
        _REMOTE, MessageType.CLAIM, _claim(), _Recorder()
    )
    assert disposition is FrameDisposition.LOCAL


async def test_unpeered_key_is_local() -> None:
    # a key no peering enumerates resolves to no domain -> a local frame
    disposition = await _hub()._authorise_federation(
        _REMOTE, MessageType.CLAIM, _claim(key_id=_LOCAL_KEY_ID), _Recorder()
    )
    assert disposition is FrameDisposition.LOCAL


async def test_cert_read_that_raises_degrades_to_local() -> None:
    # Reading the live certificate can raise on a socket that closed or never
    # finished its handshake, and an injected cert source is arbitrary code. Such a
    # failure must degrade the frame to the local path, never crash the frame loop.
    def _raising_cert_source(_websocket: Any) -> bytes | None:
        raise OSError("socket closed during certificate read")

    hub = SynapseHub(
        hub_id="syn-a",
        require_per_message_auth=True,
        signed_event_trust_bundle=_trust_bundle(),
        federation_bundle=FederationBundle([_peer(scope=(ScopeGrant(CLAIM, _NAMESPACE),))]),
        federation_cert_source=_raising_cert_source,
    )
    disposition = await hub._authorise_federation(_REMOTE, MessageType.CLAIM, _claim(), _Recorder())
    assert disposition is FrameDisposition.LOCAL


# --- cross-domain authorisation: allow within scope, deny every other way ---


async def test_cross_domain_within_scope_allows() -> None:
    recorder = _Recorder()
    disposition = await _hub()._authorise_federation(_REMOTE, MessageType.CLAIM, _claim(), recorder)
    assert disposition is FrameDisposition.ALLOW_CROSS_DOMAIN
    assert recorder.sent == []  # an allow routes the frame; it sends nothing here


async def test_cross_domain_out_of_scope_denies() -> None:
    recorder = _Recorder()
    # the peering grants MESSAGE, not CLAIM, so a claim is out of scope
    hub = _hub(scope=(ScopeGrant(MESSAGE, _NAMESPACE),))
    disposition = await hub._authorise_federation(_REMOTE, MessageType.CLAIM, _claim(), recorder)
    assert disposition is FrameDisposition.DENY
    assert recorder.sent[0]["federation_reason"] == "out_of_scope"
    assert recorder.sent[0]["federation_domain"] == _DOMAIN


async def test_cross_domain_in_an_ungranted_namespace_denies() -> None:
    recorder = _Recorder()
    # the sender's namespace OTHER is not one the peering grants
    disposition = await _hub()._authorise_federation(
        "OTHER/remote", MessageType.CLAIM, _claim(sender="OTHER/remote"), recorder
    )
    assert disposition is FrameDisposition.DENY
    assert recorder.sent[0]["federation_reason"] == "namespace_not_granted"


async def test_cross_domain_without_per_message_auth_denies() -> None:
    recorder = _Recorder()
    # a hub that does not require per-message auth cannot bind the signature -> denied
    hub = _hub(require_auth=False)
    disposition = await hub._authorise_federation(_REMOTE, MessageType.CLAIM, _claim(), recorder)
    assert disposition is FrameDisposition.DENY
    assert recorder.sent[0]["federation_reason"] == "signature_not_verified"


# --- end-to-end: the gate is wired into the live frame path over a real socket ---


def _signed_claim(private_key: Ed25519PrivateKey, key_id: str, task_id: str, nonce: str) -> str:
    frame = sign_event_frame(
        build_envelope(
            _REMOTE, "claim", target="System", task_id=task_id, project=_NAMESPACE, now=1.0
        ),
        key_id=key_id,
        private_key=private_key,
        nonce=nonce,
        sequence=1,
        signed_at=time.time(),
    )
    return json.dumps(frame)


def _e2e_hub(private_key: Ed25519PrivateKey, *, scope: tuple[ScopeGrant, ...]) -> SynapseHub:
    key = EventSignatureKey.from_private_key(
        key_id=_KEY_ID,
        private_key=private_key,
        senders=frozenset({_REMOTE}),
        projects=frozenset({_NAMESPACE}),
    )
    trust = EventSignatureTrustBundle(
        keys={_KEY_ID: key},
        replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
    )
    return SynapseHub(
        hub_id="syn-a",
        require_per_message_auth=True,
        signed_event_trust_bundle=trust,
        federation_bundle=FederationBundle([_peer(scope=scope)]),
        federation_cert_source=lambda _websocket: _DER,
    )


async def test_e2e_cross_domain_claim_is_granted() -> None:
    private_key = Ed25519PrivateKey.generate()
    hub = _e2e_hub(private_key, scope=(ScopeGrant(CLAIM, _NAMESPACE),))
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(_signed_claim(private_key, _KEY_ID, "T1", "n1"))
            granted = await read_until_type(websocket, "claim_granted")
    assert granted["task_id"] == "T1"


async def test_e2e_cross_domain_out_of_scope_is_denied() -> None:
    private_key = Ed25519PrivateKey.generate()
    # the peering grants MESSAGE, not CLAIM: the local path (ACL off) would grant this
    # claim, so a denial proves the federation gate is authoritative on the live path.
    hub = _e2e_hub(private_key, scope=(ScopeGrant(MESSAGE, _NAMESPACE),))
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(_signed_claim(private_key, _KEY_ID, "T1", "n1"))
            denied = await read_until_type(websocket, "error")
    assert denied["federation_reason"] == "out_of_scope"
    assert denied["federation_domain"] == _DOMAIN


async def test_e2e_local_signed_frame_is_unaffected() -> None:
    # a frame signed with a local key (no peering enumerates it) resolves to no domain
    # and takes the ordinary local path even though a federation bundle is configured.
    private_key = Ed25519PrivateKey.generate()
    local_key = EventSignatureKey.from_private_key(
        key_id=_LOCAL_KEY_ID,
        private_key=private_key,
        senders=frozenset({_REMOTE}),
        projects=frozenset({_NAMESPACE}),
    )
    trust = EventSignatureTrustBundle(
        keys={_LOCAL_KEY_ID: local_key},
        replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=16),
    )
    hub = SynapseHub(
        hub_id="syn-a",
        require_per_message_auth=True,
        signed_event_trust_bundle=trust,
        federation_bundle=FederationBundle([_peer(scope=(ScopeGrant(CLAIM, _NAMESPACE),))]),
        federation_cert_source=lambda _websocket: _DER,
    )
    async with running_hub(hub) as (_, uri):
        async with connect(uri) as websocket:
            await read_until_type(websocket, "welcome")
            await websocket.send(_signed_claim(private_key, _LOCAL_KEY_ID, "T9", "n9"))
            granted = await read_until_type(websocket, "claim_granted")
    assert granted["task_id"] == "T9"

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the connection-identity admission gate

from __future__ import annotations

from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.hub_identity_gate import (
    IDENTITY_BINDING_CLOSE_CODE,
    HubIdentityGate,
)
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
    sign_event_frame,
)

_SENDER = "SYNAPSE-CHANNEL/claude-2759"
_NOW = 1000.0


class _FakeWebSocket:
    def __init__(self) -> None:
        self.closed: tuple[int, str] | None = None

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)


def _bundle(private_key: Ed25519PrivateKey, *, sender: str = _SENDER) -> EventSignatureTrustBundle:
    key = EventSignatureKey.from_private_key(
        key_id="k", private_key=private_key, senders=frozenset({sender})
    )
    return EventSignatureTrustBundle(
        keys={"k": key},
        replay_cache=MessageReplayCache(window_seconds=10.0, max_entries=64),
    )


def _signed_registration(
    private_key: Ed25519PrivateKey, *, sender: str = _SENDER
) -> dict[str, Any]:
    frame = {"sender": sender, "type": "heartbeat", "target": "System", "payload": "online"}
    return sign_event_frame(
        frame, key_id="k", private_key=private_key, nonce="n1", sequence=1, signed_at=_NOW
    )


def _gate(**overrides: Any) -> tuple[HubIdentityGate, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    async def send_json(_ws: Any, data: dict[str, Any]) -> None:
        sent.append(data)

    def system(message: str, **fields: Any) -> dict[str, Any]:
        return {"message": message, **fields}

    kwargs: dict[str, Any] = {
        "require_identity_binding": True,
        "identity_trust_bundle": None,
        "send_json": send_json,
        "system": system,
        "clock": lambda: _NOW,
    }
    kwargs.update(overrides)
    return HubIdentityGate(**kwargs), sent


async def test_binding_off_admits_any_socket() -> None:
    gate, sent = _gate(require_identity_binding=False)
    ws = _FakeWebSocket()

    assert await gate.verify_identity(_SENDER, {"sender": _SENDER}, ws) is True
    assert ws.closed is None
    assert sent == []


async def test_valid_signed_registration_is_admitted() -> None:
    private_key = Ed25519PrivateKey.generate()
    gate, sent = _gate(identity_trust_bundle=_bundle(private_key))
    ws = _FakeWebSocket()

    assert await gate.verify_identity(_SENDER, _signed_registration(private_key), ws) is True
    assert ws.closed is None
    assert sent == []


async def test_unsigned_frame_is_refused_and_closed() -> None:
    private_key = Ed25519PrivateKey.generate()
    gate, sent = _gate(identity_trust_bundle=_bundle(private_key))
    ws = _FakeWebSocket()

    assert await gate.verify_identity(_SENDER, {"sender": _SENDER}, ws) is False
    assert ws.closed == (IDENTITY_BINDING_CLOSE_CODE, "identity binding failed")
    assert sent and sent[0]["verification_result"] == "missing_signature"


async def test_required_but_no_bundle_fails_closed() -> None:
    gate, sent = _gate(identity_trust_bundle=None)
    ws = _FakeWebSocket()

    assert await gate.verify_identity(_SENDER, {"sender": _SENDER}, ws) is False
    assert ws.closed == (IDENTITY_BINDING_CLOSE_CODE, "identity binding failed")
    assert sent and sent[0]["verification_result"] == "unknown_key"


async def test_wrong_identity_is_refused() -> None:
    private_key = Ed25519PrivateKey.generate()
    gate, sent = _gate(identity_trust_bundle=_bundle(private_key))
    ws = _FakeWebSocket()
    # Signed and claimed as a different identity than the key is bound to.
    frame = _signed_registration(private_key, sender="SYNAPSE-CHANNEL/evil")

    assert await gate.verify_identity("SYNAPSE-CHANNEL/evil", frame, ws) is False
    assert sent and sent[0]["verification_result"] == "sender_mismatch"

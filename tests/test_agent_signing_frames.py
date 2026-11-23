# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — signed frames stay byte-identical across the signing move
"""Registration, chat, session and advertisement frames with a frozen clock."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import ClientConnection

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.frame_signing_client import ClientFrameSession
from synapse_channel.core.identity_keys import write_signing_key
from synapse_channel.core.protocol import MessageType


class _Socket:
    """Fake socket that records sends and ends the read loop at once."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, raw: str | bytes) -> None:
        self.sent.append(raw.decode() if isinstance(raw, bytes) else raw)

    def __aiter__(self) -> _Socket:
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration


class _Open:
    def __init__(self, socket: _Socket) -> None:
        self._socket = socket

    async def __aenter__(self) -> _Socket:
        return self._socket

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.core.protocol.time.time", lambda: 1_700_000_000.0)
    monkeypatch.setattr("synapse_channel.core.message_auth.time.time", lambda: 1_700_000_000.0)
    monkeypatch.setattr(
        "synapse_channel.core.message_auth.time.time_ns", lambda: 1_700_000_000_000_000_000
    )

    def nonce(_size: int = 18) -> str:
        return "fixed-nonce"

    outbound = importlib.import_module("synapse_channel.client.agent_outbound_base")
    if hasattr(outbound, "secrets"):
        monkeypatch.setattr(outbound.secrets, "token_urlsafe", nonce)
    try:
        signing = importlib.import_module("synapse_channel.client.agent_signing_identity")
    except ImportError:
        return
    monkeypatch.setattr(signing.secrets, "token_urlsafe", nonce)


def _agent(tmp_path: Path) -> SynapseAgent:
    identity = tmp_path / "id.pem"
    card = tmp_path / "card.pem"
    write_signing_key(identity, Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33))))
    write_signing_key(card, Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65))))
    return SynapseAgent(
        "proj/seat",
        verbose=False,
        machine_identity=False,
        identity_key_path=str(identity),
        identity_key_id="seat-key",
        per_message_auth_key_id="auth",
        per_message_auth_secret=b"secret",
        capability_card_key_path=str(card),
        capability_card_key_id="proj:card",
        capability_card_project="proj",
        request_lease=True,
        owner_lease="held-token",
        takeover=True,
        mailbox=True,
        mailbox_for="proj/seat",
        roles=("worker",),
        wake_capability="direct",
    )


async def test_signed_frames_stay_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch)
    agent = _agent(tmp_path)
    socket = _Socket()

    def open_socket(*_args: object, **_kwargs: object) -> _Open:
        return _Open(socket)

    monkeypatch.setattr("synapse_channel.client.agent_lifecycle.connect", open_socket)
    await agent.connect()
    agent.transport.connection = cast(ClientConnection, socket)
    await agent.send_message(MessageType.CLAIM, target="System", payload="task-a")
    agent.session.frame_session = ClientFrameSession("hub-a", "session-nonce")
    await agent.send_message(MessageType.CHAT, target="all", payload="hello")
    agent.session.frame_session = None
    await agent.advertise(description="reads", skills=("review",))
    frames = [json.loads(raw) for raw in socket.sent]
    assert [frame["type"] for frame in frames] == [
        MessageType.HEARTBEAT,
        MessageType.CLAIM,
        MessageType.CHAT,
        MessageType.ADVERTISE,
    ]
    rendered = json.dumps(frames, sort_keys=True, separators=(",", ":"))
    pinned = Path(__file__).with_name("signing_frames.json").read_text().strip()
    assert rendered == pinned

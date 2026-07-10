# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the agent client signs its registration under identity binding

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hub_e2e_helpers import running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_identity_gate import IDENTITY_BINDING_CLOSE_CODE
from synapse_channel.core.identity_binding import verify_registration
from synapse_channel.core.identity_keys import generate_signing_key, write_signing_key
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
    SignedEventVerificationResult,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.machine_identity import MACHINE_KEY_ID_PREFIX

_SENDER = "proj/claude"
_KEY_ID = "k"


class _CaptureConnection:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, raw: str) -> None:
        self.sent.append(raw)


def _bundle(private_key: Ed25519PrivateKey) -> EventSignatureTrustBundle:
    key = EventSignatureKey.from_private_key(
        key_id=_KEY_ID, private_key=private_key, senders=frozenset({_SENDER})
    )
    return EventSignatureTrustBundle(
        keys={_KEY_ID: key},
        replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=64),
    )


def _binding_hub(private_key: Ed25519PrivateKey) -> SynapseHub:
    return SynapseHub(
        hub_id="idb", identity_trust_bundle=_bundle(private_key), require_identity_binding=True
    )


async def test_registration_heartbeat_is_identity_signed(tmp_path: Path) -> None:
    key_path = tmp_path / "id.pem"
    private_key = generate_signing_key()
    write_signing_key(key_path, private_key)
    agent = SynapseAgent(
        _SENDER, uri="ws://unused", identity_key_path=str(key_path), identity_key_id=_KEY_ID
    )
    agent.connection = _CaptureConnection()  # type: ignore[assignment]

    await agent.send_message(
        MessageType.HEARTBEAT, target="System", payload="online", sign_identity=True
    )

    frame = json.loads(agent.connection.sent[0])  # type: ignore[union-attr]
    assert frame["signature"]["key_id"] == _KEY_ID
    result = verify_registration(
        frame,
        trust_bundle=_bundle(private_key),
        now=frame["signature"]["signed_at"],
        required_sender=_SENDER,
    )
    assert result is SignedEventVerificationResult.VALID


async def test_keepalive_heartbeat_is_not_identity_signed(tmp_path: Path) -> None:
    key_path = tmp_path / "id.pem"
    write_signing_key(key_path, generate_signing_key())
    agent = SynapseAgent(
        _SENDER, uri="ws://unused", identity_key_path=str(key_path), identity_key_id=_KEY_ID
    )
    agent.connection = _CaptureConnection()  # type: ignore[assignment]

    # A keepalive leaves sign_identity at its default False.
    await agent.send_message(MessageType.HEARTBEAT, target="System", payload="online")

    assert "signature" not in json.loads(agent.connection.sent[0])  # type: ignore[union-attr]


async def test_opted_out_agent_signs_nothing() -> None:
    agent = SynapseAgent(_SENDER, uri="ws://unused", machine_identity=False)
    agent.connection = _CaptureConnection()  # type: ignore[assignment]

    await agent.send_message(
        MessageType.HEARTBEAT, target="System", payload="online", sign_identity=True
    )

    assert "signature" not in json.loads(agent.connection.sent[0])  # type: ignore[union-attr]


async def test_agent_presents_the_machine_identity_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain construction signs with the zero-config machine key.

    This is the F9 slice-0 contract behind incident 2026-07-10T1603: `arm`
    pinned a name and every other verb — none of which wired the machine
    identity — was then refused with `signature missing`. The default makes
    every verb that builds an agent sign uniformly, so the lockout class
    cannot recur through a forgotten call site.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    agent = SynapseAgent(_SENDER, uri="ws://unused")
    agent.connection = _CaptureConnection()  # type: ignore[assignment]

    await agent.send_message(
        MessageType.HEARTBEAT, target="System", payload="online", sign_identity=True
    )

    frame = json.loads(agent.connection.sent[0])  # type: ignore[union-attr]
    assert frame["signature"]["key_id"].startswith(MACHINE_KEY_ID_PREFIX)


async def test_an_explicit_key_wins_over_the_machine_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    key_path = tmp_path / "explicit.pem"
    write_signing_key(key_path, generate_signing_key())
    agent = SynapseAgent(
        _SENDER, uri="ws://unused", identity_key_path=str(key_path), identity_key_id=_KEY_ID
    )
    agent.connection = _CaptureConnection()  # type: ignore[assignment]

    await agent.send_message(
        MessageType.HEARTBEAT, target="System", payload="online", sign_identity=True
    )

    frame = json.loads(agent.connection.sent[0])  # type: ignore[union-attr]
    assert frame["signature"]["key_id"] == _KEY_ID


def test_core_only_default_degrades_to_unsigned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the cryptography extra, the default construction stays unsigned.

    The 0.99.1 regression class: the machine-identity default must never turn
    a core-only install into a crash — resolution degrades to nothing and the
    agent behaves exactly like an opted-out one.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    for name in list(sys.modules):
        if name == "cryptography" or name.startswith("cryptography."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "cryptography", None)

    agent = SynapseAgent(_SENDER, uri="ws://unused")

    assert agent._identity_key is None
    assert agent._identity_key_id == ""


async def test_keyed_agent_is_admitted_by_a_binding_hub_end_to_end(tmp_path: Path) -> None:
    key_path = tmp_path / "id.pem"
    private_key = generate_signing_key()
    write_signing_key(key_path, private_key)
    async with running_hub(_binding_hub(private_key)) as (hub, uri):
        agent = SynapseAgent(
            _SENDER,
            uri=uri,
            verbose=False,
            identity_key_path=str(key_path),
            identity_key_id=_KEY_ID,
        )
        task = asyncio.create_task(agent.connect())
        try:
            assert await agent.wait_until_ready(3.0)
            # Give the signed registration a moment to bind, then confirm the roster.
            for _ in range(30):
                if _SENDER in hub.online_agents():
                    break
                await asyncio.sleep(0.05)
            assert _SENDER in hub.online_agents()
        finally:
            agent.running = False
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_unsigned_agent_is_refused_by_a_binding_hub_end_to_end(tmp_path: Path) -> None:
    private_key = generate_signing_key()
    async with running_hub(_binding_hub(private_key)) as (hub, uri):
        agent = SynapseAgent(_SENDER, uri=uri, verbose=False, machine_identity=False)
        await asyncio.wait_for(agent.connect(), timeout=3.0)

        assert agent.last_close_code == IDENTITY_BINDING_CLOSE_CODE
        assert _SENDER not in hub.online_agents()

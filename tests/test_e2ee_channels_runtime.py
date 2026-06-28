# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-surface tests for encrypted channel payloads
"""End-to-end encrypted payload runtime tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel import cli_messaging
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.payload_crypto import (
    PAYLOAD_PLACEHOLDER,
    PayloadContext,
    decrypt_payload,
    load_payload_key,
)
from synapse_channel.core.protocol import MessageType


async def test_encrypted_channel_send_delivers_ciphertext_only_to_member(
    tmp_path: Path,
) -> None:
    key_path = generate_key_file(tmp_path / "payload.key")
    async with running_hub(SynapseHub()) as (hub, uri):
        alice = await connect_agent("alice", uri)
        bob = await connect_agent("bob", uri)
        try:
            await alice.agent.channel_create("ops")
            await alice.recorder.wait_for(
                lambda item: item.get("type") == "channel_result" and item.get("ok") is True
            )
            await bob.agent.channel_join("ops")
            await bob.recorder.wait_for(
                lambda item: item.get("type") == "channel_result" and item.get("ok") is True
            )
            await close_agents(alice)

            code = await cli_messaging._send(
                uri=uri,
                name="alice",
                target="all",
                message="operator-only release note",
                wait_seconds=0.0,
                channel="ops",
                encrypt_key_file=str(key_path),
                encrypt_key_id="ops:v1",
                encrypt_recipients=["bob"],
            )
            message = await bob.recorder.wait_for(
                lambda item: item.get("type") == "chat" and item.get("sender") == "alice"
            )
        finally:
            await close_agents(alice, bob)

    assert code == 0
    assert message["payload"] == PAYLOAD_PLACEHOLDER
    assert "operator-only" not in str(message)
    assert hub.chat_history == []
    plaintext = decrypt_payload(
        message["encrypted"],
        load_payload_key(key_path),
        context=PayloadContext(
            message_type=MessageType.CHAT,
            sender="alice",
            target="all",
            channel="ops",
        ),
    )
    assert plaintext == "operator-only release note"


async def test_encrypted_listen_prints_decrypted_payload(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key_path = generate_key_file(tmp_path / "payload.key")
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("observer", uri)
        listen_task = asyncio.create_task(
            cli_messaging._listen(
                uri=uri,
                name="bob",
                for_name="bob",
                max_messages=1,
                decrypt_key_file=str(key_path),
            )
        )
        sender = await connect_agent("alice-observer", uri)
        try:
            await observer.recorder.wait_for(
                lambda item: item.get("type") == "presence_update" and item.get("agent") == "bob"
            )
            code = await cli_messaging._send(
                uri=uri,
                name="alice",
                target="bob",
                message="decryptable direct note",
                wait_seconds=0.0,
                encrypt_key_file=str(key_path),
                encrypt_key_id="direct:v1",
                encrypt_recipients=["bob"],
            )
            listen_code = await listen_task
        finally:
            await close_agents(sender, observer)

    assert code == 0
    assert listen_code == 0
    out = capsys.readouterr().out
    assert "alice: decryptable direct note" in out
    assert PAYLOAD_PLACEHOLDER not in out

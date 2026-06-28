# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-socket tests for private-channel routing

from __future__ import annotations

from typing import Any

from websockets.asyncio.client import connect

from hub_e2e_helpers import collect_available, read_until_type, running_hub, send_json
from synapse_channel.core.hub import SynapseHub


async def _bind(websocket: Any, name: str) -> None:
    """Register and name-bind a raw socket by sending one heartbeat."""
    await send_json(websocket, sender=name, type="heartbeat", target="System", payload="online")


async def test_channel_chat_reaches_only_members() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        async with connect(uri) as alpha, connect(uri) as beta, connect(uri) as gamma:
            await _bind(alpha, "ALPHA")
            await _bind(beta, "BETA")
            await _bind(gamma, "GAMMA")

            await send_json(alpha, sender="ALPHA", type="channel_create", channel="c", label="C")
            created = await read_until_type(alpha, "channel_result")
            assert created["ok"] is True
            assert created["channel"] == "c"

            await send_json(beta, sender="BETA", type="channel_join", channel="c")
            joined = await read_until_type(beta, "channel_result")
            assert joined["ok"] is True
            assert sorted(joined["members"]) == ["ALPHA", "BETA"]

            await send_json(alpha, sender="ALPHA", type="chat", channel="c", payload="secret")

            delivered = await read_until_type(beta, "chat")
            assert delivered["payload"] == "secret"
            assert delivered["channel"] == "c"

            # GAMMA is online and bound but not a member: it must not receive the body.
            gamma_messages = await collect_available(gamma, duration=0.25)
            assert not any(
                message.get("type") == "chat" and message.get("channel") == "c"
                for message in gamma_messages
            )


async def test_channel_chat_refuses_a_non_member_sender() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        async with connect(uri) as alpha, connect(uri) as gamma:
            await _bind(alpha, "ALPHA")
            await _bind(gamma, "GAMMA")
            await send_json(alpha, sender="ALPHA", type="channel_create", channel="c")
            await read_until_type(alpha, "channel_result")

            await send_json(gamma, sender="GAMMA", type="chat", channel="c", payload="intrude")
            refusal = await read_until_type(gamma, "error")

            assert "not a member of channel 'c'" in refusal["payload"]
            # ALPHA, the only member, receives nothing from the refused send.
            alpha_messages = await collect_available(alpha, duration=0.2)
            assert not any(
                message.get("type") == "chat" and message.get("channel") == "c"
                for message in alpha_messages
            )


async def test_channel_create_join_leave_and_list_lifecycle() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        async with connect(uri) as alpha:
            await _bind(alpha, "ALPHA")

            await send_json(alpha, sender="ALPHA", type="channel_create", channel="ops")
            assert (await read_until_type(alpha, "channel_result"))["ok"] is True
            # Re-creating the same channel is refused.
            await send_json(alpha, sender="ALPHA", type="channel_create", channel="ops")
            assert (await read_until_type(alpha, "channel_result"))["ok"] is False

            await send_json(alpha, sender="ALPHA", type="channel_list_request")
            listing = await read_until_type(alpha, "channel_list")
            assert listing["channels"] == ["ops"]

            await send_json(alpha, sender="ALPHA", type="channel_leave", channel="ops")
            assert (await read_until_type(alpha, "channel_result"))["ok"] is True
            await send_json(alpha, sender="ALPHA", type="channel_list_request")
            after = await read_until_type(alpha, "channel_list")
            assert after["channels"] == []

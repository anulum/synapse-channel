# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub stale-recipient warning: present is not the same as reachable

from __future__ import annotations

import json
import time
from typing import Any

from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType


class _Clock:
    """A hand-advanced monotonic clock, so staleness is deterministic in a test."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _FakeSocket:
    """A minimal websocket the hub can bind, send to, and read a remote host from."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    @property
    def remote_address(self) -> tuple[str, int]:
        return ("test", 0)

    def messages_of_type(self, msg_type: str) -> list[dict[str, Any]]:
        return [message for message in self.sent if message.get("type") == msg_type]


def _register(name: str) -> str:
    return json.dumps(
        {"sender": name, "type": MessageType.HEARTBEAT, "target": "System", "payload": "online"}
    )


def _keepalive(name: str) -> str:
    return json.dumps(
        {"sender": name, "type": MessageType.HEARTBEAT, "target": "System", "payload": "alive"}
    )


def _chat(sender: str, target: str, payload: str = "hi") -> str:
    return json.dumps(
        {"sender": sender, "type": MessageType.CHAT, "target": target, "payload": payload}
    )


def _who_request(name: str) -> str:
    return json.dumps(
        {"sender": name, "type": MessageType.WHO_REQUEST, "target": "System", "payload": "who"}
    )


class TestReactionTracking:
    async def test_registration_seeds_the_grace_window(self) -> None:
        clock = _Clock()
        clock.t = 4.0
        hub = SynapseHub(warn_stale_recipients=True, clock=clock)
        socket = _FakeSocket()
        hub.clients.add_client(socket)

        await hub.handle_message(_register("BETA"), socket)

        assert hub._recipient_liveness.last_reaction_at("BETA") == 4.0

    async def test_a_keepalive_heartbeat_does_not_refresh_liveness(self) -> None:
        clock = _Clock()
        hub = SynapseHub(warn_stale_recipients=True, clock=clock)
        socket = _FakeSocket()
        hub.clients.add_client(socket)

        await hub.handle_message(_register("BETA"), socket)  # seeds at t=0
        clock.t = 50.0
        await hub.handle_message(_keepalive("BETA"), socket)  # a keepalive is not a reaction

        assert hub._recipient_liveness.last_reaction_at("BETA") == 0.0

    async def test_a_genuine_reaction_refreshes_liveness(self) -> None:
        clock = _Clock()
        hub = SynapseHub(warn_stale_recipients=True, clock=clock)
        socket = _FakeSocket()
        hub.clients.add_client(socket)

        await hub.handle_message(_register("BETA"), socket)  # seeds at t=0
        clock.t = 50.0
        await hub.handle_message(_chat("BETA", "all"), socket)  # a chat proves the agent acted

        assert hub._recipient_liveness.last_reaction_at("BETA") == 50.0

    async def test_disabled_hub_tracks_no_reactions(self) -> None:
        hub = SynapseHub()  # warning off by default
        socket = _FakeSocket()
        hub.clients.add_client(socket)

        await hub.handle_message(_register("BETA"), socket)
        await hub.handle_message(_chat("BETA", "all"), socket)

        assert hub._recipient_liveness.last_reaction_at("BETA") is None


class TestStaleRecipientWarning:
    async def _hub_with_sender_and_deaf_recipient(
        self, clock: _Clock, **kwargs: Any
    ) -> tuple[SynapseHub, _FakeSocket]:
        hub = SynapseHub(recipient_liveness_window=10.0, clock=clock, **kwargs)
        beta = _FakeSocket()
        hub.clients.add_client(beta)
        await hub.handle_message(_register("BETA"), beta)  # BETA registers at t=0, then goes deaf
        alpha = _FakeSocket()
        hub.clients.add_client(alpha)
        clock.t = 5.0
        await hub.handle_message(_register("ALPHA"), alpha)
        clock.t = 100.0  # BETA's t=0 reaction is now far outside the window
        return hub, alpha

    async def test_directed_message_to_a_deaf_recipient_warns_the_sender(self) -> None:
        clock = _Clock()
        hub, alpha = await self._hub_with_sender_and_deaf_recipient(
            clock, warn_stale_recipients=True
        )

        await hub.handle_message(_chat("ALPHA", "BETA", "are you there"), alpha)

        warnings = alpha.messages_of_type(MessageType.RECIPIENT_LIVENESS_WARNING)
        assert len(warnings) == 1
        assert warnings[0]["stale_recipients"] == ["BETA"]
        assert warnings[0]["target"] == "ALPHA"
        assert warnings[0]["message_target"] == "BETA"

    async def test_no_warning_when_the_recipient_has_a_live_waiter(self) -> None:
        clock = _Clock()
        hub, alpha = await self._hub_with_sender_and_deaf_recipient(
            clock, warn_stale_recipients=True
        )
        hub.clients.set_agent_socket("BETA-rx", object())  # BETA is armed after all
        hub.state.last_seen["BETA-rx"] = time.time()  # with a fresh keepalive

        await hub.handle_message(_chat("ALPHA", "BETA", "ping"), alpha)

        assert alpha.messages_of_type(MessageType.RECIPIENT_LIVENESS_WARNING) == []

    async def test_no_warning_when_the_feature_is_disabled(self) -> None:
        clock = _Clock()
        hub, alpha = await self._hub_with_sender_and_deaf_recipient(
            clock, warn_stale_recipients=False
        )

        await hub.handle_message(_chat("ALPHA", "BETA", "ping"), alpha)

        assert alpha.messages_of_type(MessageType.RECIPIENT_LIVENESS_WARNING) == []

    async def test_a_broadcast_never_warns(self) -> None:
        clock = _Clock()
        hub, alpha = await self._hub_with_sender_and_deaf_recipient(
            clock, warn_stale_recipients=True
        )

        await hub.handle_message(_chat("ALPHA", "all", "hello everyone"), alpha)

        assert alpha.messages_of_type(MessageType.RECIPIENT_LIVENESS_WARNING) == []


class TestWhoSnapshotLiveness:
    async def test_who_snapshot_carries_liveness_when_enabled(self) -> None:
        clock = _Clock()
        hub = SynapseHub(warn_stale_recipients=True, recipient_liveness_window=10.0, clock=clock)
        beta = _FakeSocket()
        hub.clients.add_client(beta)
        await hub.handle_message(_register("BETA"), beta)  # seeds BETA at t=0, then it goes deaf
        querier = _FakeSocket()
        hub.clients.add_client(querier)
        await hub.handle_message(_register("U"), querier)
        clock.t = 100.0

        await hub.handle_message(_who_request("U"), querier)

        snap = querier.messages_of_type(MessageType.WHO_SNAPSHOT)[-1]
        assert "agent_liveness" in snap
        assert snap["agent_liveness"]["BETA"]["proven_live"] is False

    async def test_who_snapshot_omits_liveness_when_disabled(self) -> None:
        hub = SynapseHub()  # off by default
        beta = _FakeSocket()
        hub.clients.add_client(beta)
        await hub.handle_message(_register("BETA"), beta)
        querier = _FakeSocket()
        hub.clients.add_client(querier)
        await hub.handle_message(_register("U"), querier)

        await hub.handle_message(_who_request("U"), querier)

        snap = querier.messages_of_type(MessageType.WHO_SNAPSHOT)[-1]
        assert "agent_liveness" not in snap

# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub stale-recipient warning: present is not the same as reachable

from __future__ import annotations

import json
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


class TestRecipientsWithoutLiveWaiter:
    def test_disabled_flags_nobody_even_with_a_stale_record(self) -> None:
        hub = SynapseHub()  # warning off by default
        hub.clients.set_agent_socket("BETA", object())
        hub._recipient_liveness.touch("BETA", 0.0)

        assert hub.recipients_without_live_waiter(["BETA"]) == ()

    def test_a_present_stale_recipient_without_a_waiter_is_flagged(self) -> None:
        clock = _Clock()
        hub = SynapseHub(warn_stale_recipients=True, recipient_liveness_window=10.0, clock=clock)
        hub.clients.set_agent_socket("BETA", object())
        hub._recipient_liveness.touch("BETA", 0.0)
        clock.t = 20.0

        assert hub.recipients_without_live_waiter(["BETA"]) == ("BETA",)

    def test_a_recipient_with_no_record_is_flagged(self) -> None:
        clock = _Clock()
        hub = SynapseHub(warn_stale_recipients=True, recipient_liveness_window=10.0, clock=clock)
        hub.clients.set_agent_socket("BETA", object())
        clock.t = 3.0

        assert hub.recipients_without_live_waiter(["BETA"]) == ("BETA",)

    def test_a_live_rx_waiter_sidecar_proves_liveness(self) -> None:
        clock = _Clock()
        hub = SynapseHub(warn_stale_recipients=True, recipient_liveness_window=10.0, clock=clock)
        hub.clients.set_agent_socket("BETA", object())
        hub.clients.set_agent_socket("BETA-rx", object())  # an armed waiter
        hub._recipient_liveness.touch("BETA", 0.0)
        clock.t = 20.0  # stale by reaction, but the waiter vouches for it

        assert hub.recipients_without_live_waiter(["BETA"]) == ()

    def test_a_recent_reaction_proves_liveness_without_a_waiter(self) -> None:
        clock = _Clock()
        hub = SynapseHub(warn_stale_recipients=True, recipient_liveness_window=10.0, clock=clock)
        hub.clients.set_agent_socket("BETA", object())
        hub._recipient_liveness.touch("BETA", 15.0)
        clock.t = 20.0  # only 5s since the reaction, inside the window

        assert hub.recipients_without_live_waiter(["BETA"]) == ()

    def test_only_the_stale_recipients_are_returned_in_order(self) -> None:
        clock = _Clock()
        hub = SynapseHub(warn_stale_recipients=True, recipient_liveness_window=10.0, clock=clock)
        for name in ("A", "B", "C"):
            hub.clients.set_agent_socket(name, object())
        hub._recipient_liveness.touch("A", 0.0)  # will be stale
        hub._recipient_liveness.touch("B", 18.0)  # fresh
        # C has no record → stale
        clock.t = 20.0

        assert hub.recipients_without_live_waiter(["A", "B", "C"]) == ("A", "C")


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


class TestRosterLiveness:
    def test_disabled_returns_an_empty_map(self) -> None:
        hub = SynapseHub()  # off by default
        hub.clients.set_agent_socket("BETA", object())

        assert hub.roster_liveness() == {}

    def test_a_recent_reaction_is_proven_live_with_its_age(self) -> None:
        clock = _Clock()
        hub = SynapseHub(warn_stale_recipients=True, recipient_liveness_window=10.0, clock=clock)
        hub.clients.set_agent_socket("BETA", object())
        hub._recipient_liveness.touch("BETA", 5.0)
        clock.t = 10.0

        assert hub.roster_liveness()["BETA"] == {
            "proven_live": True,
            "has_waiter": False,
            "last_reaction_age": 5.0,
        }

    def test_a_live_waiter_is_proven_live_even_when_stale(self) -> None:
        clock = _Clock()
        hub = SynapseHub(warn_stale_recipients=True, recipient_liveness_window=10.0, clock=clock)
        hub.clients.set_agent_socket("BETA", object())
        hub.clients.set_agent_socket("BETA-rx", object())
        hub._recipient_liveness.touch("BETA", 0.0)
        clock.t = 100.0

        info = hub.roster_liveness()["BETA"]
        assert info["proven_live"] is True
        assert info["has_waiter"] is True

    def test_a_deaf_agent_is_not_proven_live_and_reports_its_silence(self) -> None:
        clock = _Clock()
        hub = SynapseHub(warn_stale_recipients=True, recipient_liveness_window=10.0, clock=clock)
        hub.clients.set_agent_socket("BETA", object())
        hub._recipient_liveness.touch("BETA", 0.0)
        clock.t = 50.0

        assert hub.roster_liveness()["BETA"] == {
            "proven_live": False,
            "has_waiter": False,
            "last_reaction_age": 50.0,
        }

    def test_an_agent_that_never_reacted_has_a_none_age(self) -> None:
        clock = _Clock()
        hub = SynapseHub(warn_stale_recipients=True, recipient_liveness_window=10.0, clock=clock)
        hub.clients.set_agent_socket("BETA", object())  # present but never touched
        clock.t = 3.0

        assert hub.roster_liveness()["BETA"] == {
            "proven_live": False,
            "has_waiter": False,
            "last_reaction_age": None,
        }

    def test_waiter_sidecars_are_not_annotated_as_agents(self) -> None:
        hub = SynapseHub(warn_stale_recipients=True, recipient_liveness_window=10.0)
        hub.clients.set_agent_socket("BETA", object())
        hub.clients.set_agent_socket("BETA-rx", object())

        result = hub.roster_liveness()
        assert "BETA" in result
        assert "BETA-rx" not in result


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

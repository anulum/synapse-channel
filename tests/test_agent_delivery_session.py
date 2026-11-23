# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a delivery session owns the welcome and the mailbox cursor
"""Welcome binding, the mailbox cursor and the delivery incarnation."""

from __future__ import annotations

from typing import Any

import pytest

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.client.agent_delivery_session import AgentDeliverySession
from synapse_channel.client.frame_signing_client import REQUIRED, ClientFrameSession
from synapse_channel.core.protocol import WIRE_PROTOCOL_VERSION


def _session(**overrides: Any) -> AgentDeliverySession:
    values: dict[str, Any] = {
        "mailbox": False,
        "mailbox_since_seq": 0,
        "mailbox_for": "",
        "mailbox_advance": None,
        "delivery_capabilities": None,
    }
    values.update(overrides)
    return AgentDeliverySession(
        mailbox=values["mailbox"],
        mailbox_since_seq=values["mailbox_since_seq"],
        mailbox_for=values["mailbox_for"],
        mailbox_advance=values["mailbox_advance"],
        delivery_capabilities=values["delivery_capabilities"],
    )


def test_the_constructor_accepts_keywords_only() -> None:
    factory: Any = AgentDeliverySession
    with pytest.raises(TypeError):
        factory(False, 0, "", None, None)


def test_unsupported_capabilities_are_refused() -> None:
    with pytest.raises(
        ValueError, match="delivery capabilities must name supported modes and qualities"
    ):
        _session(delivery_capabilities={"interrupt": "imaginary"})


def test_capabilities_are_copied_and_mint_a_token() -> None:
    capabilities = {"interrupt": "native"}
    session = _session(delivery_capabilities=capabilities)
    capabilities["steer"] = "emulated"
    assert session.delivery_capabilities == {"interrupt": "native"}
    fields = session.registration_fields()
    assert fields["delivery_capabilities"] == {"interrupt": "native"}
    assert len(fields["delivery_session_token"]) == 64


def test_an_empty_capability_map_still_mints_a_token() -> None:
    session = _session(delivery_capabilities={})
    assert session.registration_fields()["delivery_capabilities"] == {}
    assert session.registration_fields()["delivery_session_token"]


def test_registration_omits_delivery_when_there_are_no_capabilities() -> None:
    assert _session().registration_fields() == {}


def test_heartbeat_fields_follow_the_mailbox_cursor() -> None:
    assert _session().heartbeat_fields() == {}
    absent = _session(mailbox=True, mailbox_since_seq=-4, mailbox_for="proj/other")
    assert absent.heartbeat_fields() == {
        "mailbox": True,
        "mailbox_cursor": "absent",
        "mailbox_for": "proj/other",
    }
    resumed = _session(mailbox=True, mailbox_since_seq=3)
    assert resumed.heartbeat_fields() == {"mailbox": True, "since_seq": 3}
    assert resumed.mailbox_cursor == 3


def test_a_welcome_binds_the_hub_and_a_required_signing_session() -> None:
    session = _session()
    session.bind_welcome({"hub_id": "hub-a", "protocol_version": WIRE_PROTOCOL_VERSION})
    assert session.hub_id == "hub-a"
    assert session.hub_protocol_version == WIRE_PROTOCOL_VERSION
    assert session.frame_session is None
    session.bind_welcome(
        {
            "hub_id": "hub-b",
            "protocol_version": True,
            "frame_signatures": REQUIRED,
            "session_nonce": "nonce-1",
        }
    )
    assert session.hub_id == "hub-b"
    assert session.hub_protocol_version is None
    assert session.frame_session == ClientFrameSession("hub-b", "nonce-1")


def test_a_missing_welcome_hub_stays_unknown() -> None:
    session = _session()
    session.bind_welcome({})
    assert session.hub_id == "unknown"
    assert session.hub_protocol_version is None


def test_only_a_hex_incarnation_arms_delivery() -> None:
    session = _session()
    for incarnation in ("abc", None, 7, "A" * 64, "g" * 64):
        session.bind_incarnation(incarnation)
    assert session.delivery_incarnation == ""
    assert not session.delivery_ready_event.is_set()
    incarnation = "ab" * 32
    session.bind_incarnation(incarnation)
    assert session.delivery_incarnation == incarnation
    assert session.delivery_ready_event.is_set()


def test_the_cursor_moves_only_forward() -> None:
    session = _session(mailbox=True, mailbox_since_seq=4)
    session.advance_cursor(4)
    session.advance_cursor(2)
    assert session.mailbox_cursor == 4
    session.advance_cursor(9)
    assert session.mailbox_cursor == 9


def test_reset_clears_the_signing_session_and_keeps_the_cursor() -> None:
    session = _session(mailbox=True, mailbox_since_seq=5)
    session.bind_welcome(
        {
            "hub_id": "hub-a",
            "protocol_version": 7,
            "frame_signatures": REQUIRED,
            "session_nonce": "nonce-1",
        }
    )
    session.bind_incarnation("cd" * 32)
    session.reset_attempt()
    assert session.frame_session is None
    assert session.delivery_incarnation == ""
    assert not session.delivery_ready_event.is_set()
    assert session.hub_id == "hub-a"
    assert session.hub_protocol_version == 7
    assert session.mailbox_cursor == 5


def test_ending_an_attempt_keeps_the_signing_session() -> None:
    session = _session()
    session.frame_session = ClientFrameSession("hub-a", "nonce-1")
    session.bind_incarnation("ef" * 32)
    session.end_attempt()
    assert session.frame_session == ClientFrameSession("hub-a", "nonce-1")
    assert session.delivery_incarnation == ""
    assert not session.delivery_ready_event.is_set()


def test_the_agent_facade_reads_the_session_and_refuses_assignment() -> None:
    agent = SynapseAgent(
        "proj/seat",
        verbose=False,
        machine_identity=False,
        mailbox=True,
        mailbox_for="proj/other",
        mailbox_since_seq=2,
        delivery_capabilities={"follow_up": "emulated"},
    )
    assert agent.mailbox is True
    assert agent.mailbox_for == "proj/other"
    assert agent.mailbox_cursor == 2
    assert agent.delivery_capabilities == {"follow_up": "emulated"}
    assert agent.delivery_ready_event is agent.session.delivery_ready_event
    assert agent.hub_id == "unknown"
    for name in (
        "hub_id",
        "hub_protocol_version",
        "frame_session",
        "delivery_capabilities",
        "delivery_incarnation",
        "delivery_ready_event",
        "mailbox",
        "mailbox_for",
        "mailbox_advance",
        "mailbox_cursor",
    ):
        with pytest.raises(AttributeError):
            setattr(agent, name, None)

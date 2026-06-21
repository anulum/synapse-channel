# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the on-wire message envelope builders

from __future__ import annotations

from synapse_channel.protocol import (
    RESOURCE_TYPE_ALIASES,
    SENDER_HUB,
    MessageType,
    build_envelope,
    is_recipient,
    system_message,
)


def test_message_type_values_are_stable_wire_strings() -> None:
    assert MessageType.CHAT == "chat"
    assert MessageType.CLAIM_GRANTED == "claim_granted"
    assert MessageType.NAME_CONFLICT == "name_conflict"
    assert MessageType.RESUME_REQUEST == "resume_request"
    assert MessageType.RESUME_SNAPSHOT == "resume_snapshot"
    assert MessageType.WAIT_REQUEST == "wait_request"
    assert MessageType.WAIT_DENIED == "wait_denied"


def test_resource_aliases_cover_accepted_inbound_types() -> None:
    assert RESOURCE_TYPE_ALIASES == {"resource", "resource_offer", "offer_resource"}


def test_build_envelope_defaults() -> None:
    msg = build_envelope("USER", MessageType.CHAT, payload="hi", now=1700.0)
    assert msg == {
        "sender": "USER",
        "target": "all",
        "type": "chat",
        "payload": "hi",
        "timestamp": 1700.0,
    }


def test_build_envelope_merges_extra_after_base_fields() -> None:
    msg = build_envelope(
        "A",
        MessageType.CLAIM,
        target="System",
        payload="T1",
        now=1.0,
        task_id="T1",
        note="work",
    )
    assert msg["target"] == "System"
    assert msg["task_id"] == "T1"
    assert msg["note"] == "work"


def test_build_envelope_uses_system_clock_when_now_is_none() -> None:
    before = __import__("time").time()
    msg = build_envelope("A", MessageType.HEARTBEAT)
    assert msg["timestamp"] >= before


def test_system_message_sets_hub_sender_and_id() -> None:
    msg = system_message(
        "Welcome",
        hub_id="syn-123",
        msg_type=MessageType.WELCOME,
        target="self",
        now=2.0,
        online_agents=["A"],
    )
    assert msg["sender"] == SENDER_HUB
    assert msg["hub_id"] == "syn-123"
    assert msg["type"] == "welcome"
    assert msg["target"] == "self"
    assert msg["timestamp"] == 2.0
    assert msg["online_agents"] == ["A"]


def test_system_message_defaults_to_system_type_and_broadcast() -> None:
    msg = system_message("note", hub_id="h", now=3.0)
    assert msg["type"] == "system"
    assert msg["target"] == "all"


def test_system_message_uses_system_clock_when_now_is_none() -> None:
    before = __import__("time").time()
    msg = system_message("note", hub_id="h")
    assert msg["timestamp"] >= before


def test_is_recipient_broadcast_and_empty() -> None:
    assert is_recipient("all", "B") is True
    assert is_recipient("", "B") is True


def test_is_recipient_single_and_several() -> None:
    assert is_recipient("B", "B") is True
    assert is_recipient("B", "C") is False
    assert is_recipient("B, C ,D", "C") is True
    assert is_recipient("B,C", "Z") is False


def test_is_recipient_glob_groups() -> None:
    assert is_recipient("quantum/*", "quantum/claude-7f3a")
    assert is_recipient("quantum/claude-*", "quantum/claude-7f3a")
    assert not is_recipient("quantum/*", "other/codex-1")
    assert is_recipient("quantum/*,other/codex-1", "other/codex-1")


def test_is_directed_excludes_broadcast() -> None:
    from synapse_channel.protocol import is_directed

    assert is_directed("quantum/*", "quantum/claude-1")
    assert is_directed("B", "B")
    assert not is_directed("all", "quantum/claude-1")
    assert not is_directed("", "B")

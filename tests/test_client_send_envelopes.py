# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real WebSocket tests for client send envelopes

from __future__ import annotations

from client_helpers import connected_recording_agent, wait_for_recorded_count
from synapse_channel.client.agent import SynapseAgent


async def test_send_message_noop_without_connection() -> None:
    agent = SynapseAgent("A")
    await agent.send_message("chat", payload="x")  # no connection -> silently ignored


async def test_send_helpers_emit_expected_envelopes() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.chat("hello", target="B")
        await agent.claim("  T1  ", note="work", ttl_seconds=120.0)
        await agent.claim("T2")
        await agent.release("  T3 ")
        await agent.request_state()
        await agent.request_who()
        await agent.request_history(5)
        await agent.request_history(None)
        await wait_for_recorded_count(messages, 9)

        chat, claim_full, claim_min, release, state, who, hist_n, hist_all = messages[1:]

    assert chat == {
        "sender": "A",
        "target": "B",
        "type": "chat",
        "payload": "hello",
        "timestamp": chat["timestamp"],
    }
    assert claim_full["type"] == "claim"
    assert claim_full["task_id"] == "T1"
    assert claim_full["ttl_seconds"] == 120.0
    assert "ttl_seconds" not in claim_min
    assert release["task_id"] == "T3"
    assert state["type"] == "state_request"
    assert who["type"] == "who_request"
    assert hist_n["limit"] == 5
    assert "limit" not in hist_all


async def test_log_recall_emits_envelope() -> None:
    async with connected_recording_agent("REASON") as (agent, messages):
        await agent.log_recall(
            "what blocked CONTROL?",
            returned_claim_ids=["c1", "c2"],
            was_used=True,
            abstained=False,
        )
        await wait_for_recorded_count(messages, 2)
        msg = messages[-1]

    assert msg["type"] == "recall_log"
    assert msg["sender"] == "REASON"
    assert msg["query_text"] == "what blocked CONTROL?"
    assert msg["returned_claim_ids"] == ["c1", "c2"]
    assert msg["was_used"] is True
    assert msg["abstained"] is False


async def test_log_recall_defaults_to_empty_outcome() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.log_recall("q")
        await wait_for_recorded_count(messages, 2)
        msg = messages[-1]

    assert msg["returned_claim_ids"] == []
    assert msg["was_used"] is False
    assert msg["abstained"] is False


async def test_chat_memory_tag_rides_the_envelope_only_when_set() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.chat("plain")
        await agent.chat("remember me", memory_tag="remember")
        await agent.chat("urgent", priority=True)
        await wait_for_recorded_count(messages, 4)
        plain, tagged, urgent = messages[1:]

    assert "memory_tag" not in plain and "priority" not in plain  # no envelope bloat
    assert tagged["memory_tag"] == "remember"
    assert tagged["payload"] == "remember me"
    assert urgent["priority"] is True


async def test_chat_can_carry_a_stable_client_dedupe_identity() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.chat("retryable", target="B", client_msg_id="send-42")
        await wait_for_recorded_count(messages, 2)

    assert messages[-1]["client_msg_id"] == "send-42"


async def test_record_finding_emits_envelope() -> None:
    async with connected_recording_agent("SCPN-CONTROL/agent-1") as (agent, messages):
        await agent.record_finding(
            "K_nm correlates with directed coupling at r=0.951",
            subkind="codebase-fact",
            evidence_kind="measured",
            claim_status="reference-validated",
            evidence_ref="experiments/k_nm.py:88",
            freshness="verified-at-source",
            project="SCPN-CONTROL",
            session="s1",
            source_event_seq=7,
            valid_from=2.0,
            valid_to=20.0,
            lifecycle="active",
            supersedes="prior-hash",
            checked_this_session=True,
            source_ref="r=0.951 run",
            producer_confidence=0.9,
            execution_substrate="ml350-gpu0",
            entities=["K_nm"],
            tags=["correlation"],
        )
        await wait_for_recorded_count(messages, 2)
        msg = messages[-1]

    assert msg["type"] == "finding"
    assert msg["sender"] == "SCPN-CONTROL/agent-1"
    assert msg["statement"] == "K_nm correlates with directed coupling at r=0.951"
    assert msg["subkind"] == "codebase-fact"
    assert msg["evidence_kind"] == "measured"
    assert msg["claim_status"] == "reference-validated"
    assert msg["evidence_ref"] == "experiments/k_nm.py:88"
    assert msg["freshness"] == "verified-at-source"
    assert msg["provenance"] == {"project": "SCPN-CONTROL", "session": "s1", "source_event_seq": 7}
    assert msg["validity"] == {"valid_from": 2.0, "valid_to": 20.0}
    assert msg["verified_at_source"] == {"checked_this_session": True, "source_ref": "r=0.951 run"}
    assert msg["lifecycle"] == "active"
    assert msg["supersedes"] == "prior-hash"
    assert msg["producer_confidence"] == 0.9
    assert msg["execution_substrate"] == "ml350-gpu0"
    assert msg["entities"] == ["K_nm"]
    assert msg["tags"] == ["correlation"]


async def test_record_finding_omits_unset_optionals_but_always_sends_envelopes() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        await agent.record_finding("we chose worktree isolation", subkind="decision")
        await wait_for_recorded_count(messages, 2)
        msg = messages[-1]

    # Optional scalars are omitted when unset...
    assert "evidence_kind" not in msg
    assert "claim_status" not in msg
    assert "freshness" not in msg
    assert "lifecycle" not in msg
    assert "entities" not in msg
    # ...but the structural envelopes the gate checks for are always present.
    assert msg["provenance"] == {"project": "", "session": "", "source_event_seq": None}
    assert msg["validity"] == {"valid_from": None, "valid_to": None}
    assert msg["verified_at_source"] == {"checked_this_session": False, "source_ref": ""}


async def test_ack_emits_the_seq_when_the_hub_advertises_the_ack_version() -> None:
    async with connected_recording_agent("A") as (agent, messages):
        agent.hub_protocol_version = 2
        sent = await agent.ack(7)
        await wait_for_recorded_count(messages, 2)
        ack = messages[-1]

    assert sent is True
    assert ack["type"] == "ack"
    assert ack["sender"] == "A"
    assert ack["seq"] == 7


async def test_ack_is_withheld_when_the_hub_predates_the_ack_version() -> None:
    # A hub that advertises wire version 1 does not know the ack verb, so the client must
    # not send it — the ack is withheld and nothing but the registration heartbeat is sent.
    async with connected_recording_agent("A") as (agent, messages):
        agent.hub_protocol_version = 1
        sent = await agent.ack(7)

    assert sent is False
    assert all(message.get("type") != "ack" for message in messages)


async def test_ack_is_withheld_when_the_hub_version_is_unknown() -> None:
    # The recording hub's WELCOME carries no protocol_version, so it stays None and the
    # client cannot tell the hub speaks ack — it withholds the verb rather than risk it.
    async with connected_recording_agent("A") as (agent, messages):
        assert agent.hub_protocol_version is None
        sent = await agent.ack(7)

    assert sent is False
    assert all(message.get("type") != "ack" for message in messages)


async def test_ack_is_withheld_without_a_connection() -> None:
    agent = SynapseAgent("A")
    assert await agent.ack(7) is False

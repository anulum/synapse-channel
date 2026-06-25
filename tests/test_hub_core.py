# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the routing hub using fake server sockets

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosed

from hub_helpers import FakeServerWS, _hub, _msg
from synapse_channel.core.hub import (
    MAX_LOG_PAYLOAD,
    SynapseHub,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.ratelimit import RateLimiter
from synapse_channel.core.state import MAX_OFFERS_PER_AGENT

# --- registration ------------------------------------------------------------


async def test_register_sends_welcome() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    welcome = ws.last()
    assert welcome["type"] == "welcome"
    assert welcome["hub_id"] == "syn-test"
    assert ws in hub.connected_clients


def test_redact_payload_truncates_a_long_payload() -> None:
    assert SynapseHub._redact_payload("short") == "short"
    long = "x" * 500
    redacted = SynapseHub._redact_payload(long)
    assert redacted.startswith("x" * MAX_LOG_PAYLOAD)
    assert f"(+{500 - MAX_LOG_PAYLOAD} chars)" in redacted


async def test_malformed_json_returns_error() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.handle_message("{not json", ws)
    assert ws.last()["type"] == "error"
    assert "Malformed JSON" in ws.last()["payload"]


async def test_deeply_nested_json_is_rejected_not_crashed() -> None:
    # A frame nested far past the depth guard must be refused as malformed, never
    # drive the decoder into a RecursionError that would tear down the connection.
    hub = _hub()
    ws = FakeServerWS()
    await hub.handle_message("[" * 500 + "]" * 500, ws)
    assert ws.last()["type"] == "error"
    assert "Malformed JSON" in ws.last()["payload"]


async def test_host_rate_limiter_refuses_a_flooding_host() -> None:
    hub = SynapseHub(host_rate_limiter=RateLimiter(rate_per_second=0.01, burst=2.0))
    ws = FakeServerWS(remote_address=("10.0.0.9", 5555))
    await hub.handle_message(_msg(sender="A", type="chat", payload="1"), ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="2"), ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="3"), ws)
    assert ws.last()["type"] == "error"
    assert "Host rate limit exceeded" in ws.last()["payload"]


async def test_host_rate_limiter_meters_heartbeats() -> None:
    # Unlike the per-agent limiter (which skips heartbeats), the per-host ceiling
    # charges them, so a bare-heartbeat flood from one host is bounded.
    hub = SynapseHub(host_rate_limiter=RateLimiter(rate_per_second=0.01, burst=1.0))
    ws = FakeServerWS(remote_address=("10.0.0.9", 5555))
    await hub.handle_message(_msg(sender="A", type="heartbeat"), ws)
    await hub.handle_message(_msg(sender="A", type="heartbeat"), ws)
    assert ws.last()["type"] == "error"
    assert "Host rate limit exceeded" in ws.last()["payload"]


async def test_host_rate_limiter_budgets_hosts_independently() -> None:
    hub = SynapseHub(host_rate_limiter=RateLimiter(rate_per_second=0.01, burst=1.0))
    ws1 = FakeServerWS(remote_address=("10.0.0.1", 1))
    ws2 = FakeServerWS(remote_address=("10.0.0.2", 2))
    await hub.handle_message(_msg(sender="A", type="chat", payload="x"), ws1)
    await hub.handle_message(_msg(sender="A", type="chat", payload="x"), ws1)  # ws1 over its budget
    await hub.handle_message(_msg(sender="B", type="chat", payload="y"), ws2)  # ws2 fresh budget
    assert any("Host rate limit" in raw for raw in ws1.sent)
    assert not any("Host rate limit" in raw for raw in ws2.sent)


def test_remote_host_handles_tuple_bare_and_missing() -> None:
    class _WS:
        def __init__(self, addr: Any) -> None:
            self.remote_address = addr

    assert SynapseHub._remote_host(_WS(("1.2.3.4", 9))) == "1.2.3.4"
    assert SynapseHub._remote_host(_WS("sock-path")) == "sock-path"
    assert SynapseHub._remote_host(_WS(None)) == "unknown"
    assert SynapseHub._remote_host(object()) == "unknown"


async def test_anonymous_sender_gets_generated_name() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(type="heartbeat"), ws)
    assert any(name.startswith("anon-") for name in hub.agent_sockets)


# --- chat + history ----------------------------------------------------------


async def test_chat_is_broadcast_and_recorded() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="hello"), ws)

    relayed = [m for m in ws.decoded() if m.get("type") == "chat"]
    assert relayed[-1]["payload"] == "hello"
    assert relayed[-1]["hub_id"] == "syn-test"
    assert relayed[-1]["msg_id"] == 1
    assert hub.chat_history[-1]["payload"] == "hello"


async def test_chat_preserves_supplied_timestamp_and_increments_seq() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="1", timestamp=1700.0), ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="2"), ws)

    chats = [m for m in ws.decoded() if m.get("type") == "chat"]
    assert chats[0]["timestamp"] == 1700.0
    assert [c["msg_id"] for c in chats] == [1, 2]


async def test_presence_broadcast_on_first_message() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="heartbeat"), ws)
    presence = [m for m in ws.decoded() if m.get("type") == "presence_update"]
    assert presence[-1]["event"] == "joined"
    assert presence[-1]["agent"] == "A"


# --- claim / release ---------------------------------------------------------


async def test_claim_granted_is_broadcast() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", note="x"), ws)
    granted = [m for m in ws.decoded() if m.get("type") == "claim_granted"]
    assert granted[-1]["task_id"] == "T1"
    assert granted[-1]["owner"] == "A"


async def test_claim_denied_goes_to_second_agent() -> None:
    hub = _hub()
    ws_a = FakeServerWS()
    ws_b = FakeServerWS()
    await hub.register(ws_a)
    await hub.register(ws_b)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws_a)
    await hub.handle_message(_msg(sender="B", type="claim", task_id="T1"), ws_b)
    assert ws_b.last()["type"] == "claim_denied"
    assert ws_b.last()["task_id"] == "T1"


async def test_claim_with_invalid_ttl_falls_back_to_default() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", ttl_seconds="abc"), ws)
    assert hub.state.claims["T1"].owner == "A"


async def test_claim_with_numeric_ttl_is_used() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", ttl_seconds=120), ws)
    assert "T1" in hub.state.claims


async def test_release_granted_and_denied() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    await hub.handle_message(_msg(sender="A", type="release", task_id="T1"), ws)
    assert any(m.get("type") == "release_granted" for m in ws.decoded())

    await hub.handle_message(_msg(sender="A", type="release", task_id="GHOST"), ws)
    assert ws.last()["type"] == "release_denied"


# --- task update -------------------------------------------------------------


async def test_task_update_success_is_broadcast() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    await hub.handle_message(
        _msg(sender="A", type="task_update", task_id="T1", status="working", data_ref="r"),
        ws,
    )
    updated = [m for m in ws.decoded() if m.get("type") == "task_updated"]
    assert updated[-1]["status"] == "working"
    assert updated[-1]["data_ref"] == "r"
    assert updated[-1]["version"] == 1


async def test_task_update_failure_errors_sender() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="task_update", task_id="MISSING"), ws)
    assert ws.last()["type"] == "error"


# --- resources ---------------------------------------------------------------


async def test_resource_offer_is_broadcast() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(
        _msg(sender="A", type="resource", kind="llm", name="m", capacity=2), ws
    )
    offered = [m for m in ws.decoded() if m.get("type") == "resource_offered"]
    assert offered[-1]["name"] == "m"
    assert offered[-1]["key"] == "A:llm:m"


async def test_resource_offer_missing_fields_errors() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="resource", kind="llm"), ws)
    assert ws.last()["type"] == "error"
    assert "kind+name" in ws.last()["payload"]


async def test_resource_offer_quota_is_enforced() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    for index in range(MAX_OFFERS_PER_AGENT):
        await hub.handle_message(
            _msg(sender="A", type="resource", kind="llm", name=f"m{index}"), ws
        )
    await hub.handle_message(_msg(sender="A", type="resource", kind="llm", name="overflow"), ws)
    assert ws.last()["type"] == "error"
    assert "quota" in ws.last()["payload"]


# --- snapshots ---------------------------------------------------------------


async def test_state_request_returns_snapshot() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    await hub.handle_message(_msg(sender="A", type="state_request"), ws)
    assert ws.last()["type"] == "state_snapshot"
    assert ws.last()["snapshot"]["active_claims"][0]["task_id"] == "T1"


async def test_who_request_returns_roster() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="who_request"), ws)
    snap = ws.last()
    assert snap["type"] == "who_snapshot"
    assert snap["online_agents"] == ["A"]
    assert snap["connected_clients"] == 1


async def test_history_request_variants() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    for i in range(3):
        await hub.handle_message(_msg(sender="A", type="chat", payload=str(i)), ws)

    await hub.handle_message(_msg(sender="A", type="history_request", limit=2), ws)
    limited = ws.last()
    assert limited["requested_limit"] == 2
    assert len(limited["history"]) == 2

    await hub.handle_message(_msg(sender="A", type="history_request"), ws)
    assert ws.last()["requested_limit"] == "all"

    await hub.handle_message(_msg(sender="A", type="history_request", limit="bad"), ws)
    assert ws.last()["requested_limit"] == "all"


# --- unknown + heartbeat -----------------------------------------------------


async def test_heartbeat_produces_no_route_reply() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    before = len(ws.sent)
    await hub.handle_message(_msg(sender="A", type="heartbeat"), ws)
    # Only the presence-join broadcast may appear; no per-route reply follows it.
    replies = [m for m in ws.decoded()[before:] if m.get("type") not in {"presence_update"}]
    assert replies == []


async def test_unknown_type_errors_sender() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="frobnicate"), ws)
    assert ws.last()["type"] == "error"
    assert "Unknown message type" in ws.last()["payload"]


# --- name conflicts ----------------------------------------------------------


async def test_duplicate_name_from_second_socket_is_rejected() -> None:
    hub = _hub()
    ws_a = FakeServerWS()
    ws_b = FakeServerWS()
    await hub.register(ws_a)
    await hub.register(ws_b)
    await hub.handle_message(_msg(sender="A", type="heartbeat"), ws_a)
    await hub.handle_message(_msg(sender="A", type="chat", payload="x"), ws_b)
    assert ws_b.last()["type"] == "name_conflict"
    assert ws_b.closed == (4009, "name conflict")


async def test_name_switch_on_same_socket_is_rejected() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="heartbeat"), ws)
    await hub.handle_message(_msg(sender="B", type="chat", payload="x"), ws)
    assert ws.last()["type"] == "name_conflict"
    assert ws.closed == (4009, "name switch")


# --- low-level send helper ---------------------------------------------------


async def test_send_to_agent_missing_returns_false() -> None:
    hub = _hub()
    assert await hub._send_to_agent("nobody", {"x": 1}) is False


async def test_send_to_agent_handles_send_failure() -> None:
    class BadWS:
        async def send(self, raw: str) -> None:
            raise RuntimeError("socket broke")

    hub = _hub()
    hub.agent_sockets["A"] = BadWS()
    assert await hub._send_to_agent("A", {"x": 1}) is False


# --- unregister + handler ----------------------------------------------------


async def test_unregister_removes_agent_and_announces_departure() -> None:
    hub = _hub()
    ws_a = FakeServerWS()
    ws_b = FakeServerWS()
    await hub.register(ws_a)
    await hub.register(ws_b)
    await hub.handle_message(_msg(sender="A", type="heartbeat"), ws_a)

    await hub.unregister(ws_a)
    assert "A" not in hub.agent_sockets
    assert ws_a not in hub.connected_clients
    left = [m for m in ws_b.decoded() if m.get("type") == "presence_update"]
    assert left[-1]["event"] == "left"


async def test_handler_runs_full_lifecycle() -> None:
    hub = _hub()
    ws = FakeServerWS(
        [_msg(sender="A", type="chat", payload="hi"), _msg(sender="A", type="who_request")]
    )
    await hub.handler(ws)
    # Registered (welcome), processed both messages, then unregistered.
    types = [m.get("type") for m in ws.decoded()]
    assert "welcome" in types
    assert "who_snapshot" in types
    assert ws not in hub.connected_clients


async def test_handler_swallows_connection_closed() -> None:
    class ClosingWS(FakeServerWS):
        async def __aiter__(self) -> AsyncIterator[str]:
            if self.incoming:
                yield self.incoming[0]
            raise ConnectionClosed(None, None)

    hub = _hub()
    ws = ClosingWS()
    await hub.handler(ws)  # must not raise
    assert ws not in hub.connected_clients


# --- misc --------------------------------------------------------------------


async def test_online_agents_sorted() -> None:
    hub = _hub()
    ws_a = FakeServerWS()
    ws_b = FakeServerWS()
    await hub.register(ws_a)
    await hub.register(ws_b)
    await hub.handle_message(_msg(sender="Z", type="heartbeat"), ws_a)
    await hub.handle_message(_msg(sender="A", type="heartbeat"), ws_b)
    assert hub.online_agents() == ["A", "Z"]


def test_default_hub_id_is_generated() -> None:
    hub = SynapseHub()
    assert hub.hub_id.startswith("syn-")
    assert len(hub.hub_id) == 12  # "syn-" + 8 hex chars


def test_hub_threads_per_agent_quotas_to_state() -> None:
    hub = SynapseHub(max_claims_per_agent=5, max_offers_per_agent=9, max_paths_per_claim=7)
    assert hub.state.max_claims_per_agent == 5
    assert hub.state.max_offers_per_agent == 9
    assert hub.state.max_paths_per_claim == 7


def test_hub_with_journal_threads_per_agent_quotas_to_state(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(
        journal=store, max_claims_per_agent=4, max_offers_per_agent=6, max_paths_per_claim=3
    )
    store.close()
    assert hub.state.max_claims_per_agent == 4
    assert hub.state.max_offers_per_agent == 6
    assert hub.state.max_paths_per_claim == 3


def test_hub_hints_at_compaction_when_the_log_is_oversized(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = EventStore(tmp_path / "events.db")
    store.append(EventKind.CHAT, {"msg_id": 1})
    store.append(EventKind.CHAT, {"msg_id": 2})
    with caplog.at_level("WARNING", logger="synapse.hub"):
        SynapseHub(journal=store, compact_hint_threshold=1)
    store.close()
    assert any("synapse compact" in message for message in caplog.messages)


def test_hub_stays_quiet_when_the_log_is_within_the_compact_threshold(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = EventStore(tmp_path / "events.db")
    store.append(EventKind.CHAT, {"msg_id": 1})
    with caplog.at_level("WARNING", logger="synapse.hub"):
        hub = SynapseHub(journal=store, compact_hint_threshold=100)
    store.close()
    assert hub.compact_hint_threshold == 100
    assert not any("synapse compact" in message for message in caplog.messages)


def test_compact_hint_threshold_clamps_up_to_one() -> None:
    assert SynapseHub(compact_hint_threshold=0).compact_hint_threshold == 1
    assert SynapseHub(compact_hint_threshold=-9).compact_hint_threshold == 1


@pytest.mark.parametrize("seq", [1, 2, 3])
def test_message_seq_is_monotonic(seq: int) -> None:
    hub = _hub()
    for _ in range(seq):
        value = hub._next_msg_id()
    assert value == seq

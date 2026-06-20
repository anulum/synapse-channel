# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the routing hub using fake server sockets

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosed

from synapse_channel.hub import SynapseHub
from synapse_channel.persistence import EventStore
from synapse_channel.ratelimit import RateLimiter


class FakeServerWS:
    """Stand-in for a hub-side server connection."""

    def __init__(self, incoming: list[str] | None = None) -> None:
        self.incoming = incoming or []
        self.sent: list[str] = []
        self.closed: tuple[int, str] | None = None

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)

    async def __aiter__(self) -> AsyncIterator[str]:
        for message in self.incoming:
            yield message

    def last(self) -> Any:
        return json.loads(self.sent[-1])

    def decoded(self) -> list[Any]:
        return [json.loads(raw) for raw in self.sent]


def _msg(**fields: Any) -> str:
    return json.dumps(fields)


def _hub() -> SynapseHub:
    return SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test")


# --- registration ------------------------------------------------------------


async def test_register_sends_welcome() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    welcome = ws.last()
    assert welcome["type"] == "welcome"
    assert welcome["hub_id"] == "syn-test"
    assert ws in hub.connected_clients


async def test_malformed_json_returns_error() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.handle_message("{not json", ws)
    assert ws.last()["type"] == "error"
    assert "Malformed JSON" in ws.last()["payload"]


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
    await hub.handle_message(
        _msg(sender="A", type="claim", task_id="T1", ttl_seconds="abc"), ws
    )
    assert hub.state.claims["T1"].owner == "A"


async def test_claim_with_numeric_ttl_is_used() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(
        _msg(sender="A", type="claim", task_id="T1", ttl_seconds=120), ws
    )
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
        _msg(sender="A", type="task_update", task_id="T1", status="in_progress", data_ref="r"),
        ws,
    )
    updated = [m for m in ws.decoded() if m.get("type") == "task_updated"]
    assert updated[-1]["status"] == "in_progress"
    assert updated[-1]["data_ref"] == "r"


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


@pytest.mark.parametrize("seq", [1, 2, 3])
def test_message_seq_is_monotonic(seq: int) -> None:
    hub = _hub()
    for _ in range(seq):
        value = hub._next_msg_id()
    assert value == seq


# --- scoped claims + epoch ---------------------------------------------------


async def test_claim_broadcasts_scope_and_epoch() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(
        _msg(sender="A", type="claim", task_id="T1", worktree="wt", paths=["src"]), ws
    )
    granted = [m for m in ws.decoded() if m.get("type") == "claim_granted"][-1]
    assert granted["worktree"] == "wt"
    assert granted["paths"] == ["src"]
    assert granted["epoch"] == 1


async def test_scoped_claim_overlap_is_denied() -> None:
    hub = _hub()
    ws_a = FakeServerWS()
    ws_b = FakeServerWS()
    await hub.register(ws_a)
    await hub.register(ws_b)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", paths=["src"]), ws_a)
    await hub.handle_message(
        _msg(sender="B", type="claim", task_id="T2", paths=["src/app.py"]), ws_b
    )
    assert ws_b.last()["type"] == "claim_denied"
    assert "file scope conflicts with 'T1'" in ws_b.last()["payload"]


async def test_release_with_matching_epoch_is_granted() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    epoch = hub.state.claims["T1"].epoch
    await hub.handle_message(_msg(sender="A", type="release", task_id="T1", epoch=epoch), ws)
    assert any(m.get("type") == "release_granted" for m in ws.decoded())


async def test_release_with_stale_epoch_is_denied() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    await hub.handle_message(_msg(sender="A", type="release", task_id="T1", epoch=999), ws)
    assert ws.last()["type"] == "release_denied"
    assert "epoch is stale" in ws.last()["payload"]
    assert "T1" in hub.state.claims


async def test_task_update_with_stale_epoch_errors() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    await hub.handle_message(
        _msg(sender="A", type="task_update", task_id="T1", status="done", epoch=999), ws
    )
    assert ws.last()["type"] == "error"
    assert "epoch is stale" in ws.last()["payload"]


def test_epoch_of_parsing() -> None:
    assert SynapseHub._epoch_of({"epoch": 5}) == 5
    assert SynapseHub._epoch_of({"epoch": 7.0}) == 7
    assert SynapseHub._epoch_of({"epoch": True}) is None
    assert SynapseHub._epoch_of({"epoch": "x"}) is None
    assert SynapseHub._epoch_of({}) is None


# --- durable persistence -----------------------------------------------------


async def test_hub_records_every_mutation_kind(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", paths=["src"]), ws)
    await hub.handle_message(
        _msg(sender="A", type="task_update", task_id="T1", status="in_progress"), ws
    )
    await hub.handle_message(_msg(sender="A", type="chat", payload="hello"), ws)
    await hub.handle_message(_msg(sender="A", type="resource", kind="llm", name="m"), ws)
    await hub.handle_message(_msg(sender="A", type="release", task_id="T1"), ws)

    kinds = {e.kind for e in store.read_all()}
    store.close()
    assert kinds == {"claim", "task_update", "chat", "resource", "release"}


async def test_hub_restart_replays_durable_state(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store_a = EventStore(db)
    hub_a = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-a", journal=store_a)
    ws = FakeServerWS()
    await hub_a.register(ws)
    await hub_a.handle_message(
        _msg(sender="A", type="claim", task_id="T1", paths=["src"]), ws
    )
    await hub_a.handle_message(_msg(sender="A", type="chat", payload="persist me"), ws)
    store_a.close()

    # A fresh hub over the same log resumes the live lease and history.
    store_b = EventStore(db)
    hub_b = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-b", journal=store_b)
    store_b.close()
    assert "T1" in hub_b.state.claims
    assert hub_b.state.claims["T1"].paths == ("src",)
    assert [m["payload"] for m in hub_b.chat_history] == ["persist me"]
    # Message numbering resumes past the replayed history.
    assert hub_b._message_seq == 1


async def test_hub_without_journal_keeps_log_untouched(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(default_ttl_seconds=300.0, journal=None)
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    assert store.count() == 0  # nothing written when no journal is attached
    store.close()


# --- idempotency + resume ----------------------------------------------------


async def test_duplicate_claim_is_not_reapplied() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", idem_key="k1"), ws)
    assert hub.state.claims["T1"].epoch == 1

    sent_before = len(ws.sent)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", idem_key="k1"), ws)
    # No renewal: epoch unchanged, and the cached grant is re-sent to the sender.
    assert hub.state.claims["T1"].epoch == 1
    assert ws.last()["type"] == "claim_granted"
    assert len(ws.sent) == sent_before + 1


async def test_claim_without_idem_key_renews_normally() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    assert hub.state.claims["T1"].epoch == 2  # renewed, not deduplicated


async def test_denied_claim_is_not_cached() -> None:
    hub = _hub()
    ws_a = FakeServerWS()
    ws_b = FakeServerWS()
    await hub.register(ws_a)
    await hub.register(ws_b)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", paths=["src"]), ws_a)
    await hub.handle_message(
        _msg(sender="B", type="claim", task_id="T2", paths=["src/app.py"], idem_key="k2"), ws_b
    )
    assert ws_b.last()["type"] == "claim_denied"
    assert "k2" not in hub._idempotency  # only applied mutations are cached


async def test_resume_request_returns_tail_after_cursor() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    for i in (1, 2, 3):
        await hub.handle_message(_msg(sender="A", type="chat", payload=str(i)), ws)
    await hub.handle_message(_msg(sender="A", type="resume_request", since=1), ws)
    snap = ws.last()
    assert snap["type"] == "resume_snapshot"
    assert snap["since"] == 1
    assert [m["payload"] for m in snap["messages"]] == ["2", "3"]


async def test_resume_request_invalid_cursor_defaults_to_zero() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="x"), ws)
    await hub.handle_message(_msg(sender="A", type="resume_request", since="bad"), ws)
    snap = ws.last()
    assert snap["since"] == 0
    assert len(snap["messages"]) == 1


# --- load protection: bounded history + rate limiting ------------------------


async def test_chat_history_is_bounded() -> None:
    hub = SynapseHub(hub_id="syn-test", max_history=2)
    ws = FakeServerWS()
    await hub.register(ws)
    for i in (1, 2, 3):
        await hub.handle_message(_msg(sender="A", type="chat", payload=str(i)), ws)
    assert [m["payload"] for m in hub.chat_history] == ["2", "3"]


async def test_rate_limiter_rejects_excess_messages() -> None:
    limiter = RateLimiter(rate_per_second=1.0, burst=1.0)
    hub = SynapseHub(hub_id="syn-test", rate_limiter=limiter)
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="1"), ws)  # consumes token
    await hub.handle_message(_msg(sender="A", type="chat", payload="2"), ws)  # over limit
    assert ws.last()["type"] == "error"
    assert "Rate limit" in ws.last()["payload"]
    assert [m["payload"] for m in hub.chat_history] == ["1"]  # second never applied


async def test_heartbeat_is_exempt_from_rate_limit() -> None:
    limiter = RateLimiter(rate_per_second=1.0, burst=1.0)
    hub = SynapseHub(hub_id="syn-test", rate_limiter=limiter)
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="1"), ws)  # exhaust the token
    await hub.handle_message(_msg(sender="A", type="heartbeat"), ws)  # exempt, no error
    assert ws.last()["type"] == "chat"  # heartbeat produced no rate-limit error


async def test_rate_limiter_forgets_agent_on_disconnect() -> None:
    limiter = RateLimiter(rate_per_second=1.0, burst=1.0)
    hub = SynapseHub(hub_id="syn-test", rate_limiter=limiter)
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="1"), ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="2"), ws)  # limited
    await hub.unregister(ws)

    ws2 = FakeServerWS()
    await hub.register(ws2)
    await hub.handle_message(_msg(sender="A", type="chat", payload="3"), ws2)  # fresh bucket
    assert ws2.last()["type"] == "chat"

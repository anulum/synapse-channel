# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for routing hub persistence over real sockets

from __future__ import annotations

import asyncio
from pathlib import Path

from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import collect_available, read_until_type, running_hub, send_json
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_ledger_guard import HubLedgerGuard
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.ratelimit import RateLimiter

# --- durable persistence -----------------------------------------------------


async def _connect_agent(uri: str, name: str) -> ClientConnection:
    websocket = await connect(uri)
    await read_until_type(websocket, "welcome")
    await send_json(websocket, sender=name, type="heartbeat")
    return websocket


async def test_hub_records_every_mutation_kind(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="T1", paths=["src"])
            await read_until_type(ws, "claim_granted")
            await send_json(ws, sender="A", type="task_update", task_id="T1", status="working")
            await read_until_type(ws, "task_updated")
            await send_json(ws, sender="A", type="chat", payload="hello")
            await read_until_type(ws, "chat")
            await send_json(ws, sender="A", type="resource", kind="llm", name="m")
            await read_until_type(ws, "resource_offered")
            await send_json(ws, sender="A", type="release", task_id="T1")
            await read_until_type(ws, "release_granted")

    kinds = {e.kind for e in store.read_all()}
    store.close()
    assert kinds == {"claim", "task_update", "chat", "resource", "release"}


async def test_hub_restart_replays_durable_state(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store_a = EventStore(db)
    hub_a = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-a", journal=store_a)
    async with running_hub(hub_a) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="T1", paths=["src"])
            await read_until_type(ws, "claim_granted")
            await send_json(ws, sender="A", type="chat", payload="persist me")
            await read_until_type(ws, "chat")
    store_a.close()

    store_b = EventStore(db)
    hub_b = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-b", journal=store_b)
    store_b.close()
    assert "T1" in hub_b.state.claims
    assert hub_b.state.claims["T1"].paths == ("src",)
    assert [m["payload"] for m in hub_b.chat_history] == ["persist me"]
    assert hub_b._message_seq == 1


async def test_hub_restart_replays_the_idempotency_guard(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store_a = EventStore(db)
    hub_a = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-a", journal=store_a)
    async with running_hub(hub_a) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="T1", idem_key="k1")
            await read_until_type(ws, "claim_granted")
            await send_json(ws, sender="A", type="release", task_id="T1")
            await read_until_type(ws, "release_granted")
    assert EventKind.IDEMPOTENCY in {e.kind for e in store_a.read_all()}
    store_a.close()

    store_b = EventStore(db)
    hub_b = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-b", journal=store_b)
    async with running_hub(hub_b) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="T1", idem_key="k1")
            replayed = await read_until_type(ws, "claim_granted")
    store_b.close()

    key = HubLedgerGuard.idempotency_key({"sender": "A", "type": "claim", "idem_key": "k1"})
    assert key in hub_b._idempotency
    assert "T1" not in hub_b.state.claims
    assert replayed["type"] == "claim_granted"


async def test_hub_without_journal_still_guards_in_memory() -> None:
    hub = SynapseHub(default_ttl_seconds=300.0, journal=None)
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="T1", idem_key="k9")
            await read_until_type(ws, "claim_granted")

    key = HubLedgerGuard.idempotency_key({"sender": "A", "type": "claim", "idem_key": "k9"})
    assert key in hub._idempotency


async def test_hub_without_journal_keeps_log_untouched(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(default_ttl_seconds=300.0, journal=None)
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="T1")
            await read_until_type(ws, "claim_granted")

    assert store.count() == 0
    store.close()


# --- idempotency + resume ----------------------------------------------------


async def test_duplicate_claim_is_not_reapplied() -> None:
    hub = SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test")
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="T1", idem_key="k1")
            await read_until_type(ws, "claim_granted")
            assert hub.state.claims["T1"].epoch == 1
            await send_json(ws, sender="A", type="claim", task_id="T1", idem_key="k1")
            granted = await read_until_type(ws, "claim_granted")

    assert hub.state.claims["T1"].epoch == 1
    assert granted["type"] == "claim_granted"


async def test_claim_without_idem_key_renews_normally() -> None:
    hub = SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test")
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="T1")
            await read_until_type(ws, "claim_granted")
            await send_json(ws, sender="A", type="claim", task_id="T1")
            await read_until_type(ws, "claim_granted")

    assert hub.state.claims["T1"].epoch == 2


async def test_denied_claim_is_not_cached() -> None:
    hub = SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test")
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a, await _connect_agent(uri, "B") as ws_b:
            await send_json(ws_a, sender="A", type="claim", task_id="T1", paths=["src"])
            await read_until_type(ws_a, "claim_granted")
            await send_json(
                ws_b,
                sender="B",
                type="claim",
                task_id="T2",
                paths=["src/app.py"],
                idem_key="k2",
            )
            denied = await read_until_type(ws_b, "claim_denied")

    assert denied["type"] == "claim_denied"
    assert "k2" not in hub._idempotency


async def test_resume_request_returns_tail_after_cursor() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test")) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            for i in (1, 2, 3):
                await send_json(ws, sender="A", type="chat", payload=str(i))
                await read_until_type(ws, "chat")
            await send_json(ws, sender="A", type="resume_request", since=1)
            snap = await read_until_type(ws, "resume_snapshot")

    assert snap["since"] == 1
    assert [m["payload"] for m in snap["messages"]] == ["2", "3"]


async def test_resume_request_invalid_cursor_defaults_to_zero() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test")) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="chat", payload="x")
            await read_until_type(ws, "chat")
            await send_json(ws, sender="A", type="resume_request", since="bad")
            snap = await read_until_type(ws, "resume_snapshot")

    assert snap["since"] == 0
    assert len(snap["messages"]) == 1


# --- load protection: bounded history + rate limiting ------------------------


async def test_chat_history_is_bounded() -> None:
    hub = SynapseHub(hub_id="syn-test", max_history=2)
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            for i in (1, 2, 3):
                await send_json(ws, sender="A", type="chat", payload=str(i))
                await read_until_type(ws, "chat")

    assert [m["payload"] for m in hub.chat_history] == ["2", "3"]


async def test_rate_limiter_rejects_excess_messages() -> None:
    limiter = RateLimiter(rate_per_second=1.0, burst=1.0)
    hub = SynapseHub(hub_id="syn-test", rate_limiter=limiter)
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="chat", payload="1")
            await read_until_type(ws, "chat")
            await send_json(ws, sender="A", type="chat", payload="2")
            error = await read_until_type(ws, "error")

    assert "Rate limit" in error["payload"]
    assert [m["payload"] for m in hub.chat_history] == ["1"]


async def test_heartbeat_is_exempt_from_rate_limit() -> None:
    limiter = RateLimiter(rate_per_second=1.0, burst=1.0)
    hub = SynapseHub(hub_id="syn-test", rate_limiter=limiter)
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="chat", payload="1")
            await read_until_type(ws, "chat")
            await send_json(ws, sender="A", type="heartbeat")
            messages = await collect_available(ws, duration=0.05)

    assert all(m.get("type") != "error" for m in messages)


async def test_rate_limiter_forgets_agent_on_disconnect() -> None:
    limiter = RateLimiter(rate_per_second=1.0, burst=1.0)
    hub = SynapseHub(hub_id="syn-test", rate_limiter=limiter)
    async with running_hub(hub) as (_, uri):
        ws = await _connect_agent(uri, "A")
        async with ws:
            await send_json(ws, sender="A", type="chat", payload="1")
            await read_until_type(ws, "chat")
            await send_json(ws, sender="A", type="chat", payload="2")
            await read_until_type(ws, "error")
        await asyncio.sleep(0.05)
        async with await _connect_agent(uri, "A") as ws2:
            await send_json(ws2, sender="A", type="chat", payload="3")
            chat = await read_until_type(ws2, "chat")

    assert chat["payload"] == "3"


# --- typed lifecycle + CAS over the wire -------------------------------------


async def test_claim_grant_includes_version() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test")) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="T1")
            granted = await read_until_type(ws, "claim_granted")

    assert granted["version"] == 0
    assert granted["status"] == "claimed"


async def test_illegal_transition_errors_sender() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test")) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="T1")
            await read_until_type(ws, "claim_granted")
            await send_json(
                ws, sender="A", type="task_update", task_id="T1", status="input_required"
            )
            error = await read_until_type(ws, "error")

    assert "cannot transition" in error["payload"]


async def test_stale_version_update_errors_sender() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test")) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="T1")
            await read_until_type(ws, "claim_granted")
            await send_json(ws, sender="A", type="task_update", task_id="T1", status="working")
            await read_until_type(ws, "task_updated")
            await send_json(
                ws,
                sender="A",
                type="task_update",
                task_id="T1",
                note="late",
                expected_version=0,
            )
            error = await read_until_type(ws, "error")

    assert "version conflict" in error["payload"]


# --- hold-and-wait deadlock detection ----------------------------------------


async def test_wait_for_unheld_task_is_denied() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test")) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="wait_request", task_id="GHOST")
            denied = await read_until_type(ws, "wait_denied")

    assert "not claimed" in denied["payload"]


async def test_wait_for_own_task_is_denied() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test")) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="T1")
            await read_until_type(ws, "claim_granted")
            await send_json(ws, sender="A", type="wait_request", task_id="T1")
            denied = await read_until_type(ws, "wait_denied")

    assert "already hold" in denied["payload"]


async def test_wait_granted_preserves_every_holder() -> None:
    hub = SynapseHub(hub_id="syn-test")
    async with running_hub(hub) as (_, uri):
        async with (
            await _connect_agent(uri, "A") as ws_a,
            await _connect_agent(uri, "B") as ws_b,
            await _connect_agent(uri, "C") as ws_c,
        ):
            await send_json(ws_a, sender="A", type="claim", task_id="T1", paths=["src"])
            await read_until_type(ws_a, "claim_granted")
            await send_json(ws_c, sender="C", type="claim", task_id="T2", paths=["docs"])
            await read_until_type(ws_c, "claim_granted")
            await send_json(ws_b, sender="B", type="wait_request", task_id="T1")
            granted = await read_until_type(ws_b, "wait_granted")
            await send_json(ws_b, sender="B", type="wait_request", task_id="T2")
            second_grant = await read_until_type(ws_b, "wait_granted")
            assert hub._waits["B"] == {"T1", "T2"}

    assert granted["holder"] == "A"
    assert second_grant["holder"] == "C"


async def test_circular_wait_is_denied() -> None:
    hub = SynapseHub(hub_id="syn-test")
    async with running_hub(hub) as (_, uri):
        async with (
            await _connect_agent(uri, "A") as ws_a,
            await _connect_agent(uri, "B") as ws_b,
            await _connect_agent(uri, "C") as ws_c,
        ):
            await send_json(ws_a, sender="A", type="claim", task_id="T1", paths=["src"])
            await read_until_type(ws_a, "claim_granted")
            await send_json(ws_b, sender="B", type="claim", task_id="T2", paths=["tests"])
            await read_until_type(ws_b, "claim_granted")
            await send_json(ws_c, sender="C", type="claim", task_id="T3", paths=["docs"])
            await read_until_type(ws_c, "claim_granted")
            await send_json(ws_a, sender="A", type="wait_request", task_id="T2")
            assert (await read_until_type(ws_a, "wait_granted"))["holder"] == "B"
            await send_json(ws_a, sender="A", type="wait_request", task_id="T3")
            assert (await read_until_type(ws_a, "wait_granted"))["holder"] == "C"
            assert hub._waits["A"] == {"T2", "T3"}
            await send_json(ws_b, sender="B", type="wait_request", task_id="T1")
            denied = await read_until_type(ws_b, "wait_denied")

    assert "deadlock" in denied["payload"]


async def test_wait_clears_only_when_the_waited_task_is_claimed() -> None:
    hub = SynapseHub(hub_id="syn-test")
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a, await _connect_agent(uri, "B") as ws_b:
            await send_json(ws_a, sender="A", type="claim", task_id="T1", paths=["src"])
            await read_until_type(ws_a, "claim_granted")
            await send_json(ws_b, sender="B", type="wait_request", task_id="T1")
            await read_until_type(ws_b, "wait_granted")
            assert hub._waits["B"] == {"T1"}
            # An unrelated claim must NOT erase the still-open wait (WF-4).
            await send_json(ws_b, sender="B", type="claim", task_id="T3", paths=["docs"])
            await read_until_type(ws_b, "claim_granted")
            assert hub._waits["B"] == {"T1"}
            # Claiming the WAITED task clears exactly that edge.
            await send_json(ws_a, sender="A", type="release", task_id="T1")
            await read_until_type(ws_a, "release_granted")
            await send_json(ws_b, sender="B", type="claim", task_id="T1")
            await read_until_type(ws_b, "claim_granted")

    assert "B" not in hub._waits


def test_drop_waits_removes_only_the_waiters_own_edges() -> None:
    hub = SynapseHub(hub_id="syn-test")
    hub._waits = {"X": {"T1"}, "Z": {"T1", "T2"}, "W": {"T2"}}
    hub._drop_waits("X")
    assert hub._waits == {"Z": {"T1", "T2"}, "W": {"T2"}}

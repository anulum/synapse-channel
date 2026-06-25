# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the routing hub using fake server sockets

from __future__ import annotations

from pathlib import Path

from hub_helpers import FakeServerWS, _hub, _msg
from synapse_channel.core.hub import (
    SynapseHub,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.ratelimit import RateLimiter

# --- durable persistence -----------------------------------------------------


async def test_hub_records_every_mutation_kind(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", paths=["src"]), ws)
    await hub.handle_message(
        _msg(sender="A", type="task_update", task_id="T1", status="working"), ws
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
    await hub_a.handle_message(_msg(sender="A", type="claim", task_id="T1", paths=["src"]), ws)
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


async def test_hub_restart_replays_the_idempotency_guard(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store_a = EventStore(db)
    hub_a = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-a", journal=store_a)
    ws = FakeServerWS()
    await hub_a.register(ws)
    # Claim with an idempotency key, then release — the task is now free.
    await hub_a.handle_message(_msg(sender="A", type="claim", task_id="T1", idem_key="k1"), ws)
    await hub_a.handle_message(_msg(sender="A", type="release", task_id="T1"), ws)
    assert EventKind.IDEMPOTENCY in {e.kind for e in store_a.read_all()}  # guard journalled
    store_a.close()

    # A fresh hub rebuilds the at-most-once guard from the log.
    store_b = EventStore(db)
    hub_b = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-b", journal=store_b)
    assert "k1" in hub_b._idempotency  # survived the restart
    ws2 = FakeServerWS()
    await hub_b.register(ws2)
    # Re-send the SAME claim: the guard replays the original grant instead of
    # re-applying, so the released task is NOT silently re-claimed.
    await hub_b.handle_message(_msg(sender="A", type="claim", task_id="T1", idem_key="k1"), ws2)
    store_b.close()
    assert "T1" not in hub_b.state.claims  # replayed, not re-applied
    assert ws2.last()["type"] == "claim_granted"  # the original response, replayed


async def test_hub_without_journal_still_guards_in_memory(tmp_path: Path) -> None:
    # No journal: the guard works in memory (covers the no-journal _remember path).
    hub = SynapseHub(default_ttl_seconds=300.0, journal=None)
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", idem_key="k9"), ws)
    assert "k9" in hub._idempotency


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


# --- typed lifecycle + CAS over the wire -------------------------------------


async def test_claim_grant_includes_version() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    granted = [m for m in ws.decoded() if m.get("type") == "claim_granted"][-1]
    assert granted["version"] == 0
    assert granted["status"] == "claimed"


async def test_illegal_transition_errors_sender() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    await hub.handle_message(
        _msg(sender="A", type="task_update", task_id="T1", status="input_required"), ws
    )
    assert ws.last()["type"] == "error"
    assert "cannot transition" in ws.last()["payload"]


async def test_stale_version_update_errors_sender() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    await hub.handle_message(
        _msg(sender="A", type="task_update", task_id="T1", status="working"), ws
    )
    # version is now 1; a compare-and-swap against version 0 must fail.
    await hub.handle_message(
        _msg(sender="A", type="task_update", task_id="T1", note="late", expected_version=0), ws
    )
    assert ws.last()["type"] == "error"
    assert "version conflict" in ws.last()["payload"]


# --- hold-and-wait deadlock detection ----------------------------------------


async def test_wait_for_unheld_task_is_denied() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="wait_request", task_id="GHOST"), ws)
    assert ws.last()["type"] == "wait_denied"
    assert "not claimed" in ws.last()["payload"]


async def test_wait_for_own_task_is_denied() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    await hub.handle_message(_msg(sender="A", type="wait_request", task_id="T1"), ws)
    assert ws.last()["type"] == "wait_denied"
    assert "already hold" in ws.last()["payload"]


async def test_wait_granted_for_another_holder() -> None:
    hub = _hub()
    ws_a = FakeServerWS()
    ws_b = FakeServerWS()
    await hub.register(ws_a)
    await hub.register(ws_b)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", paths=["src"]), ws_a)
    await hub.handle_message(_msg(sender="B", type="wait_request", task_id="T1"), ws_b)
    assert ws_b.last()["type"] == "wait_granted"
    assert ws_b.last()["holder"] == "A"
    assert hub._waits["B"] == "A"


async def test_circular_wait_is_denied() -> None:
    hub = _hub()
    ws_a = FakeServerWS()
    ws_b = FakeServerWS()
    await hub.register(ws_a)
    await hub.register(ws_b)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", paths=["src"]), ws_a)
    await hub.handle_message(_msg(sender="B", type="claim", task_id="T2", paths=["tests"]), ws_b)
    # A waits for B (holder of T2) — fine.
    await hub.handle_message(_msg(sender="A", type="wait_request", task_id="T2"), ws_a)
    assert ws_a.last()["type"] == "wait_granted"
    # B waiting for A (holder of T1) would close the cycle A->B->A.
    await hub.handle_message(_msg(sender="B", type="wait_request", task_id="T1"), ws_b)
    assert ws_b.last()["type"] == "wait_denied"
    assert "deadlock" in ws_b.last()["payload"]


async def test_wait_clears_on_successful_claim() -> None:
    hub = _hub()
    ws_a = FakeServerWS()
    ws_b = FakeServerWS()
    await hub.register(ws_a)
    await hub.register(ws_b)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", paths=["src"]), ws_a)
    await hub.handle_message(_msg(sender="B", type="wait_request", task_id="T1"), ws_b)
    assert hub._waits["B"] == "A"
    # B claims a disjoint task -> it is no longer blocked.
    await hub.handle_message(_msg(sender="B", type="claim", task_id="T3", paths=["docs"]), ws_b)
    assert "B" not in hub._waits


def test_drop_waits_removes_waiter_and_holders() -> None:
    hub = _hub()
    hub._waits = {"X": "Y", "Z": "X", "W": "Q"}
    hub._drop_waits("X")
    # X removed as a waiter; Z->X removed (X was its holder); unrelated W->Q kept.
    assert hub._waits == {"W": "Q"}

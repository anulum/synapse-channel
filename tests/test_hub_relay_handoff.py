# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the routing hub using fake server sockets

from __future__ import annotations

import json
from pathlib import Path

from hub_helpers import FakeServerWS, _hub, _msg
from synapse_channel.core.hub import (
    SynapseHub,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.relay import decode_lite, read_jsonl_since

# --- lite relay log mirror ---------------------------------------------------


async def test_relay_log_mirrors_broadcasts_in_compact_form(tmp_path: Path) -> None:
    log = tmp_path / "relay.ndjson"
    hub = SynapseHub(hub_id="syn-test", relay_log=log)
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="chat", payload="hello"), ws)

    events, _ = read_jsonl_since(log, 0)
    # Each mirrored line is the short-key form, not the full envelope.
    assert all(set(e) <= {"v", "i", "ty", "s", "to", "p", "t", "h"} for e in events)
    decoded = [decode_lite(e) for e in events]
    chats = [d for d in decoded if d["type"] == "chat"]
    assert chats[-1]["payload"] == "hello"
    assert chats[-1]["sender"] == "A"


async def test_relay_log_written_even_without_connected_clients(tmp_path: Path) -> None:
    log = tmp_path / "relay.ndjson"
    hub = SynapseHub(hub_id="syn-test", relay_log=log)
    # No socket is registered: the mirror exists precisely so a file observer
    # can read the channel without holding a connection.
    await hub._broadcast({"type": "chat", "sender": "A", "payload": "x", "msg_id": 1})
    events, _ = read_jsonl_since(log, 0)
    assert decode_lite(events[0])["payload"] == "x"
    assert not hub.connected_clients


async def test_relay_log_is_bounded_by_trimming(tmp_path: Path) -> None:
    log = tmp_path / "relay.ndjson"
    hub = SynapseHub(hub_id="syn-test", relay_log=log, relay_max_lines=2)
    for i in range(5):
        await hub._broadcast({"type": "chat", "sender": "A", "payload": str(i), "msg_id": i})

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 5  # trimming kept the log from growing unbounded
    assert len(lines) <= hub.relay_max_lines * 2
    assert decode_lite(json.loads(lines[-1]))["payload"] == "4"  # newest survived


def test_no_relay_log_leaves_mirror_a_noop(tmp_path: Path) -> None:
    hub = SynapseHub(hub_id="syn-test", relay_log=None)
    hub._mirror_to_relay({"type": "chat", "sender": "A", "payload": "x"})
    assert hub.relay_log is None
    assert not list(tmp_path.iterdir())  # nothing was written anywhere


# --- atomic handoff ----------------------------------------------------------


async def _online(hub: SynapseHub, name: str) -> FakeServerWS:
    """Register a socket and bind ``name`` so the hub sees the agent online."""
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender=name, type="heartbeat"), ws)
    return ws


async def test_handoff_transfers_to_online_agent_and_broadcasts() -> None:
    hub = _hub()
    ws_a = await _online(hub, "A")
    await _online(hub, "B")
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", note="ctx"), ws_a)
    await hub.handle_message(_msg(sender="A", type="handoff", task_id="T1", to_agent="B"), ws_a)

    granted = [m for m in ws_a.decoded() if m.get("type") == "handoff_granted"][-1]
    assert granted["owner"] == "B"
    assert granted["previous_owner"] == "A"
    assert hub.state.claims["T1"].owner == "B"
    # The move is recorded on the blackboard for the supervisor to see.
    notes = hub.blackboard.progress
    assert notes[-1].text == "handed off to B: ctx"
    assert any(m.get("type") == "ledger_progress_posted" for m in ws_a.decoded())


async def test_handoff_to_offline_agent_is_denied() -> None:
    hub = _hub()
    ws_a = await _online(hub, "A")
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws_a)
    await hub.handle_message(_msg(sender="A", type="handoff", task_id="T1", to_agent="GHOST"), ws_a)
    assert ws_a.last()["type"] == "handoff_denied"
    assert "not online" in ws_a.last()["payload"]
    assert hub.state.claims["T1"].owner == "A"


async def test_handoff_by_non_owner_is_denied() -> None:
    hub = _hub()
    ws_a = await _online(hub, "A")
    ws_b = await _online(hub, "B")
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws_a)
    await hub.handle_message(_msg(sender="B", type="handoff", task_id="T1", to_agent="A"), ws_b)
    assert ws_b.last()["type"] == "handoff_denied"
    assert "owned by A" in ws_b.last()["payload"]


async def test_handoff_clears_recipient_wait() -> None:
    hub = _hub()
    ws_a = await _online(hub, "A")
    ws_b = await _online(hub, "B")
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", paths=["src"]), ws_a)
    await hub.handle_message(_msg(sender="B", type="wait_request", task_id="T1"), ws_b)
    assert hub._waits["B"] == "A"
    await hub.handle_message(_msg(sender="A", type="handoff", task_id="T1", to_agent="B"), ws_a)
    assert "B" not in hub._waits  # B now owns it, no longer waiting


async def test_duplicate_handoff_is_not_reapplied() -> None:
    hub = _hub()
    ws_a = await _online(hub, "A")
    await _online(hub, "B")
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws_a)
    await hub.handle_message(
        _msg(sender="A", type="handoff", task_id="T1", to_agent="B", idem_key="h1"), ws_a
    )
    epoch = hub.state.claims["T1"].epoch
    # A no longer owns T1, but the cached grant is replayed on the repeated key.
    await hub.handle_message(
        _msg(sender="A", type="handoff", task_id="T1", to_agent="B", idem_key="h1"), ws_a
    )
    assert hub.state.claims["T1"].epoch == epoch  # not re-applied
    assert ws_a.last()["type"] == "handoff_granted"


async def test_hub_replays_handoff_owner(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store_a = EventStore(db)
    hub_a = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-a", journal=store_a)
    ws_a = await _online(hub_a, "A")
    await _online(hub_a, "B")
    await hub_a.handle_message(_msg(sender="A", type="claim", task_id="T1", paths=["src"]), ws_a)
    await hub_a.handle_message(_msg(sender="A", type="handoff", task_id="T1", to_agent="B"), ws_a)
    store_a.close()

    store_b = EventStore(db)
    hub_b = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-b", journal=store_b)
    store_b.close()
    assert hub_b.state.claims["T1"].owner == "B"


async def test_handoff_journals_a_distinct_handoff_kind(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-test", journal=store)
    ws_a = await _online(hub, "A")
    await _online(hub, "B")
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1", paths=["src"]), ws_a)
    await hub.handle_message(_msg(sender="A", type="handoff", task_id="T1", to_agent="B"), ws_a)
    kinds = [e.kind for e in store.read_all()]
    store.close()
    # The handoff is journalled under its own kind, not folded into a claim.
    assert EventKind.HANDOFF in kinds
    assert kinds.count(EventKind.CLAIM) == 1  # only the original claim


async def test_checkpoint_journals_a_distinct_checkpoint_kind(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-test", journal=store)
    ws_a = await _online(hub, "A")
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws_a)
    await hub.handle_message(
        _msg(sender="A", type="checkpoint", task_id="T1", checkpoint="cp"), ws_a
    )
    kinds = [e.kind for e in store.read_all()]
    store.close()
    assert EventKind.CHECKPOINT in kinds
    assert kinds.count(EventKind.CLAIM) == 1  # the checkpoint is no longer a claim re-snapshot


async def test_handoff_carries_checkpoint() -> None:
    hub = _hub()
    ws_a = await _online(hub, "A")
    await _online(hub, "B")
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws_a)
    await hub.handle_message(
        _msg(sender="A", type="checkpoint", task_id="T1", checkpoint="cp"), ws_a
    )
    await hub.handle_message(_msg(sender="A", type="handoff", task_id="T1", to_agent="B"), ws_a)
    granted = [m for m in ws_a.decoded() if m.get("type") == "handoff_granted"][-1]
    assert granted["checkpoint"] == "cp"


# --- resumable checkpoints ---------------------------------------------------


async def test_checkpoint_saved_acks_owner() -> None:
    hub = _hub()
    ws_a = await _online(hub, "A")
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws_a)
    await hub.handle_message(
        _msg(sender="A", type="checkpoint", task_id="T1", checkpoint="cp"), ws_a
    )
    assert ws_a.last()["type"] == "checkpoint_saved"
    assert ws_a.last()["task_id"] == "T1"
    assert hub.state.claims["T1"].checkpoint == "cp"


async def test_checkpoint_by_non_owner_is_denied() -> None:
    hub = _hub()
    ws_a = await _online(hub, "A")
    ws_b = await _online(hub, "B")
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws_a)
    await hub.handle_message(
        _msg(sender="B", type="checkpoint", task_id="T1", checkpoint="cp"), ws_b
    )
    assert ws_b.last()["type"] == "checkpoint_denied"
    assert "owned by A" in ws_b.last()["payload"]


async def test_claim_grant_includes_empty_checkpoint_for_fresh_task() -> None:
    hub = _hub()
    ws = FakeServerWS()
    await hub.register(ws)
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws)
    granted = [m for m in ws.decoded() if m.get("type") == "claim_granted"][-1]
    assert granted["checkpoint"] == ""


async def test_claim_grant_resumes_checkpoint_after_expiry() -> None:
    hub = _hub()
    ws_a = await _online(hub, "A")
    ws_b = await _online(hub, "B")
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws_a)
    await hub.handle_message(
        _msg(sender="A", type="checkpoint", task_id="T1", checkpoint="cursor=9"), ws_a
    )
    hub.state.claims["T1"].lease_expires_at = 0.0  # force the lease to lapse
    hub.state.reindex_leases()  # reflect the directly-edited lease in the expiry heap
    await hub.handle_message(_msg(sender="B", type="claim", task_id="T1"), ws_b)
    granted = [m for m in ws_b.decoded() if m.get("type") == "claim_granted"][-1]
    assert granted["owner"] == "B"
    assert granted["checkpoint"] == "cursor=9"  # B resumes where A stopped


async def test_duplicate_checkpoint_is_not_reapplied() -> None:
    hub = _hub()
    ws_a = await _online(hub, "A")
    await hub.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws_a)
    await hub.handle_message(
        _msg(sender="A", type="checkpoint", task_id="T1", checkpoint="cp", idem_key="c1"), ws_a
    )
    version = hub.state.claims["T1"].version
    await hub.handle_message(
        _msg(sender="A", type="checkpoint", task_id="T1", checkpoint="cp2", idem_key="c1"), ws_a
    )
    assert hub.state.claims["T1"].version == version  # not re-applied
    assert hub.state.claims["T1"].checkpoint == "cp"  # second save ignored
    assert ws_a.last()["type"] == "checkpoint_saved"


async def test_hub_replays_checkpoint(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store_a = EventStore(db)
    hub_a = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-a", journal=store_a)
    ws_a = await _online(hub_a, "A")
    await hub_a.handle_message(_msg(sender="A", type="claim", task_id="T1"), ws_a)
    await hub_a.handle_message(
        _msg(sender="A", type="checkpoint", task_id="T1", checkpoint="cp"), ws_a
    )
    store_a.close()

    store_b = EventStore(db)
    hub_b = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-b", journal=store_b)
    store_b.close()
    assert hub_b.state.claims["T1"].checkpoint == "cp"

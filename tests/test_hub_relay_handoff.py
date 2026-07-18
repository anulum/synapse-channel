# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for relay, handoff, and checkpoints over real sockets

from __future__ import annotations

import json
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.asyncio.connection import Connection

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.relay import decode_lite, read_jsonl_since

# --- lite relay log mirror ---------------------------------------------------


async def _connect_agent(uri: str, name: str) -> Connection:
    websocket = await connect(uri)
    await read_until_type(websocket, "welcome")
    await send_json(websocket, sender=name, type="heartbeat")
    return websocket


async def test_relay_log_mirrors_broadcasts_in_compact_form(tmp_path: Path) -> None:
    log = tmp_path / "relay.ndjson"
    hub = SynapseHub(hub_id="syn-test", relay_log=log)
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="chat", payload="hello")
            await read_until_type(ws, "chat")

    events, _ = read_jsonl_since(log, 0)
    assert all(
        set(event) <= {"v", "i", "ty", "s", "to", "p", "t", "h", "c", "x"} for event in events
    )
    decoded = [decode_lite(e) for e in events]
    chats = [d for d in decoded if d["type"] == "chat"]
    assert chats[-1]["payload"] == "hello"
    assert chats[-1]["sender"] == "A"


async def test_relay_log_preserves_real_claim_grant_fields(tmp_path: Path) -> None:
    log = tmp_path / "relay.ndjson"
    hub = SynapseHub(hub_id="syn-test", relay_log=log)
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(
                ws,
                sender="A",
                type="claim",
                task_id="SCH-RELAY-1",
                note="relay fidelity",
                paths=["src/relay.py"],
                worktree="checkout-a",
            )
            wire_grant = await read_until_type(ws, "claim_granted")

    rows, _ = read_jsonl_since(log, 0)
    relay_grant = next(
        decoded for row in rows if (decoded := decode_lite(row))["type"] == "claim_granted"
    )

    for field in (
        "task_id",
        "owner",
        "note",
        "lease_expires_at",
        "status",
        "worktree",
        "paths",
        "epoch",
        "version",
        "checkpoint",
        "git",
    ):
        assert relay_grant[field] == wire_grant[field]


async def test_relay_log_is_bounded_by_trimming(tmp_path: Path) -> None:
    log = tmp_path / "relay.ndjson"
    hub = SynapseHub(hub_id="syn-test", relay_log=log, relay_max_lines=2)
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            for i in range(5):
                await send_json(ws, sender="A", type="chat", payload=str(i))
                await read_until_type(ws, "chat")

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 5
    assert len(lines) <= hub.relay_max_lines * 2
    decoded = [decode_lite(json.loads(line)) for line in lines]
    assert any(event.get("type") == "chat" and event.get("payload") == "4" for event in decoded)


def test_no_relay_log_leaves_mirror_a_noop(tmp_path: Path) -> None:
    hub = SynapseHub(hub_id="syn-test", relay_log=None)
    hub._mirror_to_relay({"type": "chat", "sender": "A", "payload": "x"})
    assert hub.relay_log is None
    assert not list(tmp_path.iterdir())


# --- atomic handoff ----------------------------------------------------------


async def test_handoff_transfers_to_online_agent_and_broadcasts() -> None:
    hub = SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test")
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a, await _connect_agent(uri, "B"):
            await send_json(ws_a, sender="A", type="claim", task_id="T1", note="ctx")
            await read_until_type(ws_a, "claim_granted")
            await send_json(ws_a, sender="A", type="handoff", task_id="T1", to_agent="B")
            granted = await read_until_type(ws_a, "handoff_granted")
            assert hub.state.claims["T1"].owner == "B"
            assert hub.blackboard.progress[-1].text == "handed off to B: ctx"

    assert granted["owner"] == "B"
    assert granted["previous_owner"] == "A"


async def test_handoff_to_offline_agent_is_denied() -> None:
    hub = SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test")
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a:
            await send_json(ws_a, sender="A", type="claim", task_id="T1")
            await read_until_type(ws_a, "claim_granted")
            await send_json(ws_a, sender="A", type="handoff", task_id="T1", to_agent="GHOST")
            denied = await read_until_type(ws_a, "handoff_denied")
            assert hub.state.claims["T1"].owner == "A"

    assert "not online" in denied["payload"]


async def test_handoff_by_non_owner_is_denied() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test")) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a, await _connect_agent(uri, "B") as ws_b:
            await send_json(ws_a, sender="A", type="claim", task_id="T1")
            await read_until_type(ws_a, "claim_granted")
            await send_json(ws_b, sender="B", type="handoff", task_id="T1", to_agent="A")
            denied = await read_until_type(ws_b, "handoff_denied")

    assert "owned by A" in denied["payload"]


async def test_handoff_clears_recipient_wait() -> None:
    hub = SynapseHub(hub_id="syn-test")
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a, await _connect_agent(uri, "B") as ws_b:
            await send_json(ws_a, sender="A", type="claim", task_id="T1", paths=["src"])
            await read_until_type(ws_a, "claim_granted")
            await send_json(ws_b, sender="B", type="wait_request", task_id="T1")
            await read_until_type(ws_b, "wait_granted")
            assert hub._waits["B"] == {"A"}
            await send_json(ws_a, sender="A", type="handoff", task_id="T1", to_agent="B")
            await read_until_type(ws_a, "handoff_granted")
            assert "B" not in hub._waits


async def test_duplicate_handoff_is_not_reapplied() -> None:
    hub = SynapseHub(hub_id="syn-test")
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a, await _connect_agent(uri, "B"):
            await send_json(ws_a, sender="A", type="claim", task_id="T1")
            await read_until_type(ws_a, "claim_granted")
            await send_json(
                ws_a,
                sender="A",
                type="handoff",
                task_id="T1",
                to_agent="B",
                idem_key="h1",
            )
            await read_until_type(ws_a, "handoff_granted")
            epoch = hub.state.claims["T1"].epoch
            await send_json(
                ws_a,
                sender="A",
                type="handoff",
                task_id="T1",
                to_agent="B",
                idem_key="h1",
            )
            replayed = await read_until_type(ws_a, "handoff_granted")

    assert hub.state.claims["T1"].epoch == epoch
    assert replayed["type"] == "handoff_granted"


async def test_hub_replays_handoff_owner(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store_a = EventStore(db)
    hub_a = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-a", journal=store_a)
    async with running_hub(hub_a) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a, await _connect_agent(uri, "B"):
            await send_json(ws_a, sender="A", type="claim", task_id="T1", paths=["src"])
            await read_until_type(ws_a, "claim_granted")
            await send_json(ws_a, sender="A", type="handoff", task_id="T1", to_agent="B")
            await read_until_type(ws_a, "handoff_granted")
    store_a.close()

    store_b = EventStore(db)
    hub_b = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-b", journal=store_b)
    store_b.close()
    assert hub_b.state.claims["T1"].owner == "B"


async def test_handoff_journals_a_distinct_handoff_kind(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-test", journal=store)
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a, await _connect_agent(uri, "B"):
            await send_json(ws_a, sender="A", type="claim", task_id="T1", paths=["src"])
            await read_until_type(ws_a, "claim_granted")
            await send_json(ws_a, sender="A", type="handoff", task_id="T1", to_agent="B")
            await read_until_type(ws_a, "handoff_granted")

    kinds = [e.kind for e in store.read_all()]
    store.close()
    assert EventKind.HANDOFF in kinds
    assert kinds.count(EventKind.CLAIM) == 1


async def test_checkpoint_journals_a_distinct_checkpoint_kind(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-test", journal=store)
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a:
            await send_json(ws_a, sender="A", type="claim", task_id="T1")
            await read_until_type(ws_a, "claim_granted")
            await send_json(ws_a, sender="A", type="checkpoint", task_id="T1", checkpoint="cp")
            await read_until_type(ws_a, "checkpoint_saved")

    kinds = [e.kind for e in store.read_all()]
    store.close()
    assert EventKind.CHECKPOINT in kinds
    assert kinds.count(EventKind.CLAIM) == 1


async def test_handoff_carries_checkpoint() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test")) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a, await _connect_agent(uri, "B"):
            await send_json(ws_a, sender="A", type="claim", task_id="T1")
            await read_until_type(ws_a, "claim_granted")
            await send_json(ws_a, sender="A", type="checkpoint", task_id="T1", checkpoint="cp")
            await read_until_type(ws_a, "checkpoint_saved")
            await send_json(ws_a, sender="A", type="handoff", task_id="T1", to_agent="B")
            granted = await read_until_type(ws_a, "handoff_granted")

    assert granted["checkpoint"] == "cp"


# --- resumable checkpoints ---------------------------------------------------


async def test_checkpoint_saved_acks_owner() -> None:
    hub = SynapseHub(hub_id="syn-test")
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a:
            await send_json(ws_a, sender="A", type="claim", task_id="T1")
            await read_until_type(ws_a, "claim_granted")
            await send_json(ws_a, sender="A", type="checkpoint", task_id="T1", checkpoint="cp")
            saved = await read_until_type(ws_a, "checkpoint_saved")
            assert hub.state.claims["T1"].checkpoint == "cp"

    assert saved["task_id"] == "T1"


async def test_checkpoint_by_non_owner_is_denied() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test")) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a, await _connect_agent(uri, "B") as ws_b:
            await send_json(ws_a, sender="A", type="claim", task_id="T1")
            await read_until_type(ws_a, "claim_granted")
            await send_json(ws_b, sender="B", type="checkpoint", task_id="T1", checkpoint="cp")
            denied = await read_until_type(ws_b, "checkpoint_denied")

    assert "owned by A" in denied["payload"]


async def test_claim_grant_includes_empty_checkpoint_for_fresh_task() -> None:
    async with running_hub(SynapseHub(hub_id="syn-test")) as (_, uri):
        async with await _connect_agent(uri, "A") as ws:
            await send_json(ws, sender="A", type="claim", task_id="T1")
            granted = await read_until_type(ws, "claim_granted")

    assert granted["checkpoint"] == ""


async def test_claim_grant_resumes_checkpoint_after_expiry() -> None:
    hub = SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test")
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a, await _connect_agent(uri, "B") as ws_b:
            await send_json(ws_a, sender="A", type="claim", task_id="T1")
            await read_until_type(ws_a, "claim_granted")
            await send_json(
                ws_a,
                sender="A",
                type="checkpoint",
                task_id="T1",
                checkpoint="cursor=9",
            )
            await read_until_type(ws_a, "checkpoint_saved")
            hub.state.claims["T1"].lease_expires_at = 0.0
            hub.state.reindex_leases()
            await send_json(ws_b, sender="B", type="claim", task_id="T1")
            for _ in range(5):
                granted = await read_until_type(ws_b, "claim_granted")
                if granted["owner"] == "B":
                    break
            else:
                raise AssertionError("B did not receive resumed claim grant")

    assert granted["owner"] == "B"
    assert granted["checkpoint"] == "cursor=9"


async def test_duplicate_checkpoint_is_not_reapplied() -> None:
    hub = SynapseHub(hub_id="syn-test")
    async with running_hub(hub) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a:
            await send_json(ws_a, sender="A", type="claim", task_id="T1")
            await read_until_type(ws_a, "claim_granted")
            await send_json(
                ws_a,
                sender="A",
                type="checkpoint",
                task_id="T1",
                checkpoint="cp",
                idem_key="c1",
            )
            await read_until_type(ws_a, "checkpoint_saved")
            version = hub.state.claims["T1"].version
            await send_json(
                ws_a,
                sender="A",
                type="checkpoint",
                task_id="T1",
                checkpoint="cp2",
                idem_key="c1",
            )
            saved = await read_until_type(ws_a, "checkpoint_saved")

    assert hub.state.claims["T1"].version == version
    assert hub.state.claims["T1"].checkpoint == "cp"
    assert saved["type"] == "checkpoint_saved"


async def test_hub_replays_checkpoint(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    store_a = EventStore(db)
    hub_a = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-a", journal=store_a)
    async with running_hub(hub_a) as (_, uri):
        async with await _connect_agent(uri, "A") as ws_a:
            await send_json(ws_a, sender="A", type="claim", task_id="T1")
            await read_until_type(ws_a, "claim_granted")
            await send_json(ws_a, sender="A", type="checkpoint", task_id="T1", checkpoint="cp")
            await read_until_type(ws_a, "checkpoint_saved")
    store_a.close()

    store_b = EventStore(db)
    hub_b = SynapseHub(default_ttl_seconds=3600.0, hub_id="syn-b", journal=store_b)
    store_b.close()
    assert hub_b.state.claims["T1"].checkpoint == "cp"

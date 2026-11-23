# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — end-to-end characterisation of heartbeat, replay and dead-letter lifecycles
"""Characterise the hub heartbeat, mailbox replay, and dead-letter lifecycles end to end."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_until_type, running_hub
from synapse_channel.core.acl import MAILBOX, AclPolicy, AclRule
from synapse_channel.core.handlers.dead_letter_escalation import escalate_dead_letter
from synapse_channel.core.handlers.heartbeat import handle_heartbeat
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_transport import OperatorRelayPeer
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.ready_dispatch import plan_dispatches
from synapse_channel.core.role_grants import RoleGrants


async def _send(ws: ClientConnection, frame: dict[str, Any]) -> None:
    await ws.send(json.dumps(frame))


async def _collect_until_type(
    ws: ClientConnection, msg_type: str, *, timeout: float = 3.0
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Drain frames until ``msg_type`` arrives, returning earlier frames and the target."""
    seen: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        assert remaining > 0, f"timed out waiting for {msg_type!r}; saw {seen!r}"
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        frame = json.loads(raw)
        if frame.get("type") == msg_type:
            return seen, frame
        seen.append(frame)


async def test_keepalive_refreshes_liveness_expires_claim_prunes_wait_not_reaction_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove hub-side heartbeat: last_seen, claim expiry, wait pruning and reaction clock."""
    now = [1000.0]
    monkeypatch.setattr("synapse_channel.core.state.time.time", lambda: now[0])
    hub = SynapseHub(
        hub_id="syn-hb-live",
        warn_stale_recipients=True,
        recipient_liveness_window=5.0,
        default_ttl_seconds=60.0,
        clock=lambda: now[0],
    )
    async with running_hub(hub) as (_, uri):
        async with (
            connect(uri) as holder_ws,
            connect(uri) as waiter_ws,
            connect(uri) as alice_ws,
            connect(uri) as alice_rx_ws,
        ):
            for ws in (holder_ws, waiter_ws, alice_ws, alice_rx_ws):
                await read_until_type(ws, MessageType.WELCOME)

            await _send(holder_ws, {"type": MessageType.HEARTBEAT, "sender": "PROJ/holder"})
            await _send(
                holder_ws,
                {
                    "type": MessageType.CLAIM,
                    "sender": "PROJ/holder",
                    "task_id": "PROJ/t1",
                    "ttl_seconds": 60.0,
                },
            )
            claim_ack = await read_until_type(holder_ws, MessageType.CLAIM_GRANTED)
            assert claim_ack["task_id"] == "PROJ/t1"

            await _send(waiter_ws, {"type": MessageType.HEARTBEAT, "sender": "PROJ/waiter"})
            await _send(
                waiter_ws,
                {"type": MessageType.WAIT_REQUEST, "sender": "PROJ/waiter", "task_id": "PROJ/t1"},
            )
            await _send(waiter_ws, {"type": MessageType.STATE_REQUEST, "sender": "PROJ/waiter"})
            await read_until_type(waiter_ws, MessageType.STATE_SNAPSHOT)
            assert "PROJ/t1" in hub.waits.get("PROJ/waiter", set())

            await _send(alice_ws, {"type": MessageType.HEARTBEAT, "sender": "PROJ/alice"})
            await _send(alice_rx_ws, {"type": MessageType.HEARTBEAT, "sender": "PROJ/alice-rx"})
            await _send(waiter_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/waiter"})
            await read_until_type(waiter_ws, MessageType.WHO_SNAPSHOT)

            # Registration seeded both last_seen and the reaction clock.
            assert hub.state.last_seen["PROJ/alice"] == 1000.0
            assert hub.roster_liveness()["PROJ/alice"]["last_reaction_age"] == 0.0

            # Advance past holder's TTL and alice's reaction window, then send keepalives only.
            now[0] = 5000.0
            await _send(alice_ws, {"type": MessageType.HEARTBEAT, "sender": "PROJ/alice"})
            await _send(alice_rx_ws, {"type": MessageType.HEARTBEAT, "sender": "PROJ/alice-rx"})
            # Use waiter_ws to fence without touching alice's reaction clock.
            await _send(waiter_ws, {"type": MessageType.STATE_REQUEST, "sender": "PROJ/waiter"})
            await read_until_type(waiter_ws, MessageType.STATE_SNAPSHOT)

            # Check alice received no error frame for its keepalive heartbeat.
            await _send(alice_rx_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/alice-rx"})
            alice_rx_prior, who_after = await _collect_until_type(
                alice_rx_ws, MessageType.WHO_SNAPSHOT
            )
            assert not any(f.get("type") == MessageType.ERROR for f in alice_rx_prior)

            assert hub.state.last_seen["PROJ/alice"] == 5000.0
            assert hub.state.last_seen["PROJ/alice-rx"] == 5000.0
            assert "PROJ/t1" not in hub.state.claims
            assert not hub.waits.get("PROJ/waiter")
            # Keepalive heartbeat MUST NOT refresh the reaction clock (age stays 4000s, not 0s).
            assert who_after["agent_liveness"]["PROJ/alice"]["last_reaction_age"] == 4000.0
            assert hub.roster_liveness()["PROJ/alice"]["last_reaction_age"] == 4000.0
            # Liveness readers consume the refreshed last_seen.
            assert hub.liveness.has_live_waiter("PROJ/alice") is True
            assert hub.liveness.stale_owner_reclaimable("PROJ/alice", ttl_seconds=60.0) is False
            assert hub.liveness.stale_owner_reclaimable("PROJ/holder", ttl_seconds=60.0) is True


async def test_registration_heartbeat_binds_roles_journals_normalises_wake_keepalive_preserves(
    tmp_path: Path,
) -> None:
    """Prove role filtering, role_binding journalling, wake normalisation and keepalive."""
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(
        hub_id="syn-hb-reg",
        journal=store,
        require_role_claim=True,
        role_grants=RoleGrants({"PROJ/reviewer": frozenset({"PROJ/bob"})}),
    )
    try:
        async with running_hub(hub) as (_, uri):
            async with connect(uri) as bob_ws, connect(uri) as sender_ws:
                await read_until_type(bob_ws, MessageType.WELCOME)
                await read_until_type(sender_ws, MessageType.WELCOME)

                # Registration declares one permitted role, one ungranted role and raw wake.
                await _send(
                    bob_ws,
                    {
                        "type": MessageType.HEARTBEAT,
                        "sender": "PROJ/bob",
                        "roles": ["PROJ/reviewer", "PROJ/ungranted", "  ", 42],
                        "wake_capability": "  DIRECT  ",
                    },
                )
                await _send(bob_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/bob"})
                prior, who = await _collect_until_type(bob_ws, MessageType.WHO_SNAPSHOT)
                assert not any(f.get("type") == MessageType.ERROR for f in prior)
                assert who["agent_roles"]["PROJ/bob"] == ["PROJ/reviewer"]
                assert who["wake_capabilities"]["PROJ/bob"] == "direct"
                assert hub.roles_of("PROJ/bob") == ("PROJ/reviewer",)

                # Ready dispatch consumes the normalised wake capability from /who.
                plan = plan_dispatches(
                    tasks=[{"task_id": "PROJ/task-1", "status": "open", "project": "PROJ"}],
                    ready_ids=frozenset({"PROJ/task-1"}),
                    claims=[],
                    cards=[{"agent": "PROJ/bob", "project": "PROJ"}],
                    online=frozenset(who["online_agents"]),
                    wake_capabilities=who["wake_capabilities"],
                    project="PROJ",
                    now=1000.0,
                )
                assert len(plan.assignments) == 1
                assert plan.assignments[0].owner == "PROJ/bob"
                assert any("wake direct" in r for r in plan.assignments[0].reasons)

                role_events_1 = [
                    e
                    for e in store.read_since(0, kinds=(EventKind.ROLE_BINDING,))
                    if e.payload.get("identity") == "PROJ/bob"
                ]
                assert len(role_events_1) == 1
                assert role_events_1[0].payload["roles"] == ["PROJ/reviewer"]

                # Re-sending the same role set journals no duplicate role_binding event.
                await _send(
                    bob_ws,
                    {
                        "type": MessageType.HEARTBEAT,
                        "sender": "PROJ/bob",
                        "roles": ["PROJ/reviewer"],
                    },
                )
                # Keepalive omitting roles and wake_capability preserves both earlier bindings.
                await _send(bob_ws, {"type": MessageType.HEARTBEAT, "sender": "PROJ/bob"})
                await _send(bob_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/bob"})
                _, who_after = await _collect_until_type(bob_ws, MessageType.WHO_SNAPSHOT)
                assert who_after["agent_roles"]["PROJ/bob"] == ["PROJ/reviewer"]
                assert who_after["wake_capabilities"]["PROJ/bob"] == "direct"
                role_events_2 = [
                    e
                    for e in store.read_since(0, kinds=(EventKind.ROLE_BINDING,))
                    if e.payload.get("identity") == "PROJ/bob"
                ]
                assert len(role_events_2) == 1

                # A role-addressed chat reaches the holder.
                await _send(sender_ws, {"type": MessageType.HEARTBEAT, "sender": "PROJ/sender"})
                await _send(
                    sender_ws,
                    {
                        "type": MessageType.CHAT,
                        "sender": "PROJ/sender",
                        "target": "PROJ/reviewer",
                        "payload": "please review",
                    },
                )
                delivered = await read_until_type(bob_ws, MessageType.CHAT)
                assert delivered["payload"] == "please review"
    finally:
        store.close()


async def test_mailbox_replay_since_seq_absent_cursor_malformed_since_keepalive_and_acl_ack(
    tmp_path: Path,
) -> None:
    """Prove mailbox replay modes, mailbox_for authorisation, keepalive and ACL-backed ack."""
    store = EventStore(tmp_path / "events.db")
    policy = AclPolicy([AclRule(MAILBOX, "agent", "PROJ/target", "OPS", "grant ops monitor")])
    hub = SynapseHub(hub_id="syn-hb-mbx", journal=store, acl_policy=policy)
    try:
        async with running_hub(hub) as (_, uri):
            # Seed role binding so role mail for PROJ/reviewer is tracked while target is offline.
            async with connect(uri) as seed_ws:
                await read_until_type(seed_ws, MessageType.WELCOME)
                await _send(
                    seed_ws,
                    {
                        "type": MessageType.HEARTBEAT,
                        "sender": "PROJ/target",
                        "roles": ["PROJ/reviewer"],
                    },
                )
                await _send(seed_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/target"})
                await read_until_type(seed_ws, MessageType.WHO_SNAPSHOT)

            # While target is offline, send two personal chats (one with receipt) and role mail.
            async with connect(uri) as sender_ws:
                await read_until_type(sender_ws, MessageType.WELCOME)
                await _send(sender_ws, {"type": MessageType.HEARTBEAT, "sender": "PROJ/sender"})
                await _send(
                    sender_ws,
                    {
                        "type": MessageType.CHAT,
                        "sender": "PROJ/sender",
                        "target": "PROJ/target",
                        "payload": "msg-1",
                        "receipt_requested": True,
                    },
                )
                imm_receipt = await read_until_type(sender_ws, MessageType.DELIVERY_RECEIPT)
                assert imm_receipt["delivered"] is False

                await _send(
                    sender_ws,
                    {
                        "type": MessageType.CHAT,
                        "sender": "PROJ/sender",
                        "target": "PROJ/target",
                        "payload": "msg-2",
                    },
                )
                await _send(
                    sender_ws,
                    {
                        "type": MessageType.CHAT,
                        "sender": "PROJ/sender",
                        "target": "PROJ/reviewer",
                        "payload": "role-msg-3",
                    },
                )
                await _send(sender_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/sender"})
                await read_until_type(sender_ws, MessageType.WHO_SNAPSHOT)

                chat_events = store.read_since(0, kinds=(EventKind.CHAT,))
                by_payload = {str(e.payload["payload"]): e.seq for e in chat_events}
                seq1, seq2, seq3 = (
                    by_payload["msg-1"],
                    by_payload["msg-2"],
                    by_payload["role-msg-3"],
                )

                # 1. Keepalive without mailbox=True NEVER replays backlog.
                async with connect(uri) as quiet_ws:
                    await read_until_type(quiet_ws, MessageType.WELCOME)
                    await _send(
                        quiet_ws,
                        {"type": MessageType.HEARTBEAT, "sender": "PROJ/target"},
                    )
                    await _send(
                        quiet_ws,
                        {"type": MessageType.WHO_REQUEST, "sender": "PROJ/target"},
                    )
                    prior, _ = await _collect_until_type(quiet_ws, MessageType.WHO_SNAPSHOT)
                    assert [f for f in prior if f.get("type") == MessageType.CHAT] == []

                # 2. Unauthorised mailbox_for falls back to sender (never leaks target's backlog).
                async with connect(uri) as eve_ws:
                    await read_until_type(eve_ws, MessageType.WELCOME)
                    await _send(
                        eve_ws,
                        {
                            "type": MessageType.HEARTBEAT,
                            "sender": "PROJ/eve",
                            "mailbox": True,
                            "mailbox_for": "PROJ/target",
                            "since_seq": 0,
                        },
                    )
                    await _send(eve_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/eve"})
                    eve_prior, _ = await _collect_until_type(eve_ws, MessageType.WHO_SNAPSHOT)
                    assert [f for f in eve_prior if f.get("type") == MessageType.CHAT] == []

                    # Unauthorised ack with spoofed mailbox_for does not settle target's receipt.
                    await _send(
                        eve_ws,
                        {
                            "type": MessageType.ACK,
                            "sender": "PROJ/eve",
                            "seq": seq1,
                            "mailbox_for": "PROJ/target",
                        },
                    )
                    await _send(eve_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/eve"})
                    await _collect_until_type(eve_ws, MessageType.WHO_SNAPSHOT)
                    assert hub.pending_receipts.peek(seq1) is not None

                # 3. Absent cursor anchors at tip, skips personal history, replays role mail.
                expected_anchor = store.max_seq()
                async with connect(uri) as absent_ws:
                    await read_until_type(absent_ws, MessageType.WELCOME)
                    await _send(
                        absent_ws,
                        {
                            "type": MessageType.HEARTBEAT,
                            "sender": "PROJ/target",
                            "roles": ["PROJ/reviewer"],
                            "mailbox": True,
                            "mailbox_cursor": "absent",
                        },
                    )
                    await _send(
                        absent_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/target"}
                    )
                    absent_prior, _ = await _collect_until_type(absent_ws, MessageType.WHO_SNAPSHOT)
                    absent_chats = [
                        f["payload"] for f in absent_prior if f.get("type") == MessageType.CHAT
                    ]
                    assert absent_chats == ["role-msg-3"]
                    anchors = [f for f in absent_prior if "mailbox_anchor" in f]
                    assert len(anchors) == 1
                    assert anchors[0]["mailbox_anchor"] == expected_anchor

                # 4. since_seq=seq1 via -rx sidecar replays strictly after seq1 and advances cursor.
                async with connect(uri) as rx_ws:
                    await read_until_type(rx_ws, MessageType.WELCOME)
                    await _send(
                        rx_ws,
                        {
                            "type": MessageType.HEARTBEAT,
                            "sender": "PROJ/target-rx",
                            "mailbox": True,
                            "mailbox_for": "PROJ/target",
                            "since_seq": seq1,
                        },
                    )
                    await _send(
                        rx_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/target-rx"}
                    )
                    rx_prior, _ = await _collect_until_type(rx_ws, MessageType.WHO_SNAPSHOT)
                    rx_seqs = [f["seq"] for f in rx_prior if f.get("type") == MessageType.CHAT]
                    assert rx_seqs == [seq2]
                    watermarks = store.read_since(0, kinds=(EventKind.MAILBOX_WATERMARK,))
                    assert any(
                        w.payload.get("identity") == "PROJ/target"
                        and w.payload.get("source") == "cursor"
                        for w in watermarks
                    )

                # 5. Malformed since_seq degrades to 0 and ACL monitor receives target backlog.
                async with connect(uri) as mon_ws:
                    await read_until_type(mon_ws, MessageType.WELCOME)
                    await _send(
                        mon_ws,
                        {
                            "type": MessageType.HEARTBEAT,
                            "sender": "OPS/monitor",
                            "mailbox": True,
                            "mailbox_for": "PROJ/target",
                            "since_seq": "malformed",
                        },
                    )
                    await _send(mon_ws, {"type": MessageType.WHO_REQUEST, "sender": "OPS/monitor"})
                    mon_prior, _ = await _collect_until_type(mon_ws, MessageType.WHO_SNAPSHOT)
                    mon_seqs = [f["seq"] for f in mon_prior if f.get("type") == MessageType.CHAT]
                    assert mon_seqs == [seq1, seq2]
                    assert seq3 not in mon_seqs  # monitor does not hold PROJ/reviewer

                    # 6. handle_ack resolves mailbox_for via ACL grant and settles receipt.
                    await _send(
                        mon_ws,
                        {
                            "type": MessageType.ACK,
                            "sender": "OPS/monitor",
                            "seq": seq1,
                            "mailbox_for": "PROJ/target",
                        },
                    )
                    deferred = await read_until_type(sender_ws, MessageType.DELIVERY_RECEIPT)
                    assert deferred["delivered"] is True
                    assert deferred["deferred"] is True
                    assert deferred["recipients"] == ["PROJ/target"]
                    assert deferred["ack_receiver"] == "OPS/monitor"
    finally:
        store.close()


async def test_registration_heartbeat_requires_literal_true_for_mailbox_replay_and_watermark(
    tmp_path: Path,
) -> None:
    """Only literal mailbox=True replays missed directed chat and advances the pending watermark."""
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-hb-bool", journal=store)
    try:
        async with running_hub(hub) as (_, uri):
            async with connect(uri) as sender_ws:
                await read_until_type(sender_ws, MessageType.WELCOME)
                await _send(sender_ws, {"type": MessageType.HEARTBEAT, "sender": "PROJ/sender"})
                await _send(
                    sender_ws,
                    {
                        "type": MessageType.CHAT,
                        "sender": "PROJ/sender",
                        "target": "PROJ/target",
                        "payload": "msg-1",
                    },
                )
                await _send(
                    sender_ws,
                    {
                        "type": MessageType.CHAT,
                        "sender": "PROJ/sender",
                        "target": "PROJ/target",
                        "payload": "msg-2",
                    },
                )
                await _send(sender_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/sender"})
                await read_until_type(sender_ws, MessageType.WHO_SNAPSHOT)

            chats = store.read_since(0, kinds=(EventKind.CHAT,))
            by_payload = {str(e.payload["payload"]): e.seq for e in chats}
            seq1, seq2 = by_payload["msg-1"], by_payload["msg-2"]
            assert hub.mailbox_pending.watermark_for("PROJ/target") == 0
            assert hub.mailbox_pending.pending_count("PROJ/target") == 2
            assert store.read_since(0, kinds=(EventKind.MAILBOX_WATERMARK,)) == []

            for non_bool_mailbox in (1, "true"):
                async with connect(uri) as target_ws:
                    await read_until_type(target_ws, MessageType.WELCOME)
                    await _send(
                        target_ws,
                        {
                            "type": MessageType.HEARTBEAT,
                            "sender": "PROJ/target",
                            "mailbox": non_bool_mailbox,
                            "since_seq": seq1,
                        },
                    )
                    await _send(
                        target_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/target"}
                    )
                    prior, _ = await _collect_until_type(target_ws, MessageType.WHO_SNAPSHOT)
                    assert [f for f in prior if f.get("type") == MessageType.CHAT] == []
                assert hub.mailbox_pending.watermark_for("PROJ/target") == 0
                assert hub.mailbox_pending.pending_count("PROJ/target") == 2
                assert store.read_since(0, kinds=(EventKind.MAILBOX_WATERMARK,)) == []

            async with connect(uri) as true_ws:
                await read_until_type(true_ws, MessageType.WELCOME)
                await _send(
                    true_ws,
                    {
                        "type": MessageType.HEARTBEAT,
                        "sender": "PROJ/target",
                        "mailbox": True,
                        "since_seq": seq1,
                    },
                )
                await _send(true_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/target"})
                true_prior, _ = await _collect_until_type(true_ws, MessageType.WHO_SNAPSHOT)
                replayed = [f["seq"] for f in true_prior if f.get("type") == MessageType.CHAT]
                assert replayed == [seq2]
            assert hub.mailbox_pending.watermark_for("PROJ/target") == seq1
            assert hub.mailbox_pending.pending_count("PROJ/target") == 1
            watermarks = store.read_since(0, kinds=(EventKind.MAILBOX_WATERMARK,))
            assert len(watermarks) == 1
            assert watermarks[0].payload["identity"] == "PROJ/target"
            assert watermarks[0].payload["through_seq"] == seq1
            assert watermarks[0].payload["source"] == "cursor"
    finally:
        store.close()


async def test_dead_letter_escalation_and_peer_forwarding_end_to_end(tmp_path: Path) -> None:
    """Prove dead-letter escalation broadcast, audit events and peer forwarding end to end."""
    store = EventStore(tmp_path / "events.db")
    forwarded: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def fake_forwarder(notice: dict[str, Any], **kwargs: Any) -> None:
        forwarded.append((dict(notice), dict(kwargs)))

    hub = SynapseHub(
        hub_id="syn-edge",
        journal=store,
        dead_letter_escalation_threshold=1,
        namespace_ownership=NamespaceOwnership(
            owners={"OWNED": "syn-owner"}, local_hub_id="syn-edge"
        ),
        relay_peers={"syn-owner": OperatorRelayPeer(uri="ws://owner/", token="tok")},
        dead_letter_forwarder=fake_forwarder,
    )
    try:
        async with running_hub(hub) as (_, uri):
            async with connect(uri) as sender_ws, connect(uri) as obs_ws:
                await read_until_type(sender_ws, MessageType.WELCOME)
                await read_until_type(obs_ws, MessageType.WELCOME)
                await _send(sender_ws, {"type": MessageType.HEARTBEAT, "sender": "PROJ/sender"})
                await _send(obs_ws, {"type": MessageType.HEARTBEAT, "sender": "PROJ/observer"})
                await _send(obs_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/observer"})
                await read_until_type(obs_ws, MessageType.WHO_SNAPSHOT)

                await _send(
                    sender_ws,
                    {
                        "type": MessageType.CHAT,
                        "sender": "PROJ/sender",
                        "target": "OWNED/missing",
                        "payload": "hello absent reader",
                    },
                )
                esc_frame = await read_until_type(obs_ws, MessageType.DEAD_LETTER_ESCALATION)
                assert esc_frame["escalation_target"] == "OWNED/missing"
                assert esc_frame["escalation_count"] == 1
                assert esc_frame["last_sender"] == "PROJ/sender"

                # Wait via fence so peer forwarding coroutine completes before asserting.
                await _send(sender_ws, {"type": MessageType.WHO_REQUEST, "sender": "PROJ/sender"})
                await read_until_type(sender_ws, MessageType.WHO_SNAPSHOT)

        esc_events = store.read_since(0, kinds=(EventKind.DEAD_LETTER_ESCALATION,))
        fwd_events = store.read_since(0, kinds=(EventKind.DEAD_LETTER_FORWARDING,))
        assert len(esc_events) == 1
        assert esc_events[0].payload["target"] == "OWNED/missing"
        assert len(fwd_events) == 1
        assert fwd_events[0].payload["direction"] == "out"
        assert fwd_events[0].payload["owner_hub_id"] == "syn-owner"
        assert len(forwarded) == 1
        notice, kwargs = forwarded[0]
        assert notice == {
            "target": "OWNED/missing",
            "count": 1,
            "origin_hub_id": "syn-edge",
            "owner_hub_id": "syn-owner",
        }
        assert kwargs == {"uri": "ws://owner/", "local_id": "syn-edge", "token": "tok"}
    finally:
        store.close()


async def test_handle_heartbeat_normalises_wake_capability_before_binding() -> None:
    """Prove handle_heartbeat itself normalises wake_capability before passing it to the hub."""
    recorded: list[tuple[str, str]] = []

    class _RecordingHub:
        def set_wake_capability(self, name: str, capability: str) -> None:
            recorded.append((name, capability))

    await handle_heartbeat(
        cast(Any, _RecordingHub()),
        "PROJ/bob",
        {"wake_capability": "  DIRECT  "},
        object(),
    )
    await handle_heartbeat(
        cast(Any, _RecordingHub()),
        "PROJ/bob",
        {"wake_capability": "bogus-mode"},
        object(),
    )
    assert recorded == [("PROJ/bob", "direct"), ("PROJ/bob", "unknown")]


async def test_escalate_dead_letter_broadcasts_and_skips_journal_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise public escalate_dead_letter directly without a journal or peer map."""
    broadcasts: list[dict[str, Any]] = []
    hub = SynapseHub(hub_id="syn-esc-unit", dead_letter_escalation_threshold=2)

    async def capture(frame: dict[str, Any]) -> None:
        broadcasts.append(dict(frame))

    monkeypatch.setattr(hub, "broadcast", capture)
    await escalate_dead_letter(hub, target="PROJ/absent", count=2, sender="PROJ/sender")
    assert len(broadcasts) == 1
    assert broadcasts[0]["type"] == MessageType.DEAD_LETTER_ESCALATION
    assert broadcasts[0]["escalation_target"] == "PROJ/absent"
    assert broadcasts[0]["escalation_count"] == 2
    assert broadcasts[0]["last_sender"] == "PROJ/sender"

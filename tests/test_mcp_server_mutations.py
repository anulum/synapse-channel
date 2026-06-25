# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Model Context Protocol bridge

from __future__ import annotations

import asyncio

from mcp_server_helpers import agent_of, drive, make_bridge
from synapse_channel.core.protocol import MessageType


async def test_claim_granted() -> None:
    bridge = make_bridge(name="me")
    reply = {"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "me"}
    out = await drive(bridge, lambda: bridge.claim("T1", ["src/a.py"]), reply)
    assert "granted" in out
    assert "src/a.py" in out
    assert ("claim", "T1", ["src/a.py"]) in agent_of(bridge).calls


async def test_claim_granted_whole_worktree() -> None:
    bridge = make_bridge(name="me")
    reply = {"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "me"}
    out = await drive(bridge, lambda: bridge.claim("T1"), reply)
    assert "whole worktree" in out


async def test_claim_denied() -> None:
    bridge = make_bridge(name="me")
    reply = {"type": MessageType.CLAIM_DENIED, "task_id": "T1", "payload": "held by ALPHA"}
    out = await drive(bridge, lambda: bridge.claim("T1"), reply)
    assert "denied" in out
    assert "ALPHA" in out


async def test_claim_grant_for_other_owner_is_not_mine() -> None:
    bridge = make_bridge(name="me", request_timeout=0.05)
    # A grant addressed to a different owner must not satisfy our claim.
    reply = {"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "OTHER"}
    out = await drive(bridge, lambda: bridge.claim("T1"), reply)
    assert "no response" in out


async def test_claim_timeout() -> None:
    bridge = make_bridge(name="me", request_timeout=0.05)
    out = await bridge.claim("T1")
    assert "no response" in out


async def test_claim_ignores_reply_for_another_task() -> None:
    bridge = make_bridge(name="me")
    task = asyncio.create_task(bridge.claim("T1"))
    for _ in range(50):
        if agent_of(bridge).calls:
            break
        await asyncio.sleep(0)
    # A grant for a different task id must not satisfy our pending claim.
    await bridge.on_message({"type": MessageType.CLAIM_GRANTED, "task_id": "OTHER", "owner": "me"})
    await bridge.on_message({"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "me"})
    out = await task
    assert "granted" in out


async def test_release_granted() -> None:
    bridge = make_bridge()
    reply = {"type": MessageType.RELEASE_GRANTED, "task_id": "T1"}
    out = await drive(bridge, lambda: bridge.release("T1"), reply)
    assert "released 'T1'" in out
    assert ("release", "T1") in agent_of(bridge).calls


async def test_release_denied() -> None:
    bridge = make_bridge()
    reply = {"type": MessageType.RELEASE_DENIED, "task_id": "T1", "payload": "not the owner"}
    out = await drive(bridge, lambda: bridge.release("T1"), reply)
    assert "denied" in out


async def test_release_timeout() -> None:
    bridge = make_bridge(request_timeout=0.05)
    out = await bridge.release("T1")
    assert "no response" in out


async def test_send_dispatches_chat() -> None:
    bridge = make_bridge()
    out = await bridge.send("ALPHA", "status?")
    assert out == "sent to ALPHA"
    assert ("chat", "ALPHA", "status?") in agent_of(bridge).calls


async def test_handoff_granted() -> None:
    bridge = make_bridge()
    reply = {"type": MessageType.HANDOFF_GRANTED, "task_id": "T1"}
    out = await drive(bridge, lambda: bridge.handoff("T1", "BETA"), reply)
    assert "handed off 'T1' to BETA" in out
    assert ("handoff", "T1", "BETA") in agent_of(bridge).calls


async def test_handoff_denied() -> None:
    bridge = make_bridge()
    reply = {"type": MessageType.HANDOFF_DENIED, "task_id": "T1", "payload": "BETA offline"}
    out = await drive(bridge, lambda: bridge.handoff("T1", "BETA"), reply)
    assert "denied" in out
    assert "BETA offline" in out


async def test_handoff_timeout() -> None:
    bridge = make_bridge(request_timeout=0.05)
    out = await bridge.handoff("T1", "BETA")
    assert "no response" in out


async def test_task_declare_posted() -> None:
    bridge = make_bridge()
    reply = {"type": MessageType.LEDGER_TASK_POSTED, "task": {"task_id": "T1", "title": "Build"}}
    out = await drive(bridge, lambda: bridge.task_declare("T1", "Build", ["T0"]), reply)
    assert "declared 'T1'" in out
    assert "Build" in out
    assert ("post_task", "T1", "Build", ("T0",)) in agent_of(bridge).calls


async def test_task_declare_timeout() -> None:
    bridge = make_bridge(request_timeout=0.05)
    out = await bridge.task_declare("T1", "Build")
    assert "no response" in out


async def test_task_update_updated() -> None:
    bridge = make_bridge()
    reply = {"type": MessageType.LEDGER_TASK_UPDATED, "task": {"task_id": "T1", "status": "done"}}
    out = await drive(bridge, lambda: bridge.task_update("T1", "done"), reply)
    assert "status=done" in out
    assert ("update_ledger_task", "T1", "done", None) in agent_of(bridge).calls


async def test_task_update_timeout() -> None:
    bridge = make_bridge(request_timeout=0.05)
    out = await bridge.task_update("T1", "done")
    assert "no response" in out

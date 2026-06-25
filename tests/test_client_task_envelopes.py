# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the async hub client using an injected transport

from __future__ import annotations

import json

from client_helpers import FakeWebSocket
from synapse_channel.client.agent import SynapseAgent


async def test_claim_sends_worktree_and_paths() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.claim("T1", note="n", worktree="wt", paths=["src", "tests"])
    sent = json.loads(ws.sent[-1])
    assert sent["type"] == "claim"
    assert sent["worktree"] == "wt"
    assert sent["paths"] == ["src", "tests"]


async def test_claim_omits_scope_when_unset() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.claim("T1")
    sent = json.loads(ws.sent[-1])
    assert "worktree" not in sent
    assert "paths" not in sent
    assert "git" not in sent


async def test_claim_sends_git_context() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    git = {"branch": "feature/x", "base": "main", "auto_release_on": "merge"}
    await agent.claim("T1", paths=["src"], git=git)
    sent = json.loads(ws.sent[-1])
    assert sent["git"] == git


async def test_release_sends_and_omits_epoch() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.release("T1", epoch=4)
    with_epoch = json.loads(ws.sent[-1])
    assert with_epoch["epoch"] == 4

    await agent.release("T1")
    without_epoch = json.loads(ws.sent[-1])
    assert "epoch" not in without_epoch


async def test_claim_and_release_send_idem_key() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.claim("T1", idem_key="k1")
    assert json.loads(ws.sent[-1])["idem_key"] == "k1"
    await agent.release("T1", idem_key="k2")
    assert json.loads(ws.sent[-1])["idem_key"] == "k2"


async def test_idem_key_omitted_when_unset() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.claim("T1")
    assert "idem_key" not in json.loads(ws.sent[-1])


async def test_request_resume_sends_cursor() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.request_resume(since=7)
    sent = json.loads(ws.sent[-1])
    assert sent["type"] == "resume_request"
    assert sent["since"] == 7


async def test_update_task_sends_lifecycle_and_cas_fields() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.update_task(
        "T1", status="working", note="n", data_ref="r", epoch=5, expected_version=2, idem_key="k"
    )
    sent = json.loads(ws.sent[-1])
    assert sent["type"] == "task_update"
    assert sent["status"] == "working"
    assert sent["epoch"] == 5
    assert sent["expected_version"] == 2
    assert sent["idem_key"] == "k"


async def test_update_task_minimal_omits_optional_fields() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.update_task("T1")
    sent = json.loads(ws.sent[-1])
    assert sent["task_id"] == "T1"
    assert "status" not in sent
    assert "expected_version" not in sent


async def test_request_wait_sends_task_id() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.request_wait("  T1  ")
    sent = json.loads(ws.sent[-1])
    assert sent["type"] == "wait_request"
    assert sent["task_id"] == "T1"


async def test_handoff_sends_full_and_minimal_envelopes() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.handoff("  T1 ", "  B ", note="over to you", epoch=3, idem_key="k")
    full = json.loads(ws.sent[-1])
    assert full["type"] == "handoff"
    assert full["task_id"] == "T1"
    assert full["to_agent"] == "B"
    assert full["note"] == "over to you"
    assert full["epoch"] == 3
    assert full["idem_key"] == "k"

    await agent.handoff("T1", "B")
    minimal = json.loads(ws.sent[-1])
    assert "note" not in minimal
    assert "epoch" not in minimal
    assert "idem_key" not in minimal


async def test_save_checkpoint_sends_full_and_minimal_envelopes() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.save_checkpoint("  T1 ", "cursor=5", epoch=2, idem_key="k")
    full = json.loads(ws.sent[-1])
    assert full["type"] == "checkpoint"
    assert full["task_id"] == "T1"
    assert full["checkpoint"] == "cursor=5"
    assert full["epoch"] == 2
    assert full["idem_key"] == "k"

    await agent.save_checkpoint("T1", "x")
    minimal = json.loads(ws.sent[-1])
    assert "epoch" not in minimal
    assert "idem_key" not in minimal

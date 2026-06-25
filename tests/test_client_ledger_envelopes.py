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


async def test_post_task_sends_full_and_minimal_envelopes() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.post_task(
        "  T1 ", "Parser", description="d", depends_on=["T0"], suggested_owner="FAST"
    )
    full = json.loads(ws.sent[-1])
    assert full["type"] == "ledger_task"
    assert full["task_id"] == "T1"
    assert full["title"] == "Parser"
    assert full["depends_on"] == ["T0"]
    assert full["suggested_owner"] == "FAST"

    await agent.post_task("T2", "Bare")
    minimal = json.loads(ws.sent[-1])
    assert "description" not in minimal
    assert "depends_on" not in minimal
    assert "suggested_owner" not in minimal


async def test_update_ledger_task_sends_status_and_owner() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.update_ledger_task("T1", status="done", suggested_owner="")
    sent = json.loads(ws.sent[-1])
    assert sent["type"] == "ledger_task_update"
    assert sent["status"] == "done"
    assert sent["suggested_owner"] == ""

    await agent.update_ledger_task("T1")
    bare = json.loads(ws.sent[-1])
    assert "status" not in bare
    assert "suggested_owner" not in bare


async def test_post_progress_sends_kind_and_text() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.post_progress("  T1 ", "blocked on review", kind="blocked")
    sent = json.loads(ws.sent[-1])
    assert sent["type"] == "ledger_progress"
    assert sent["task_id"] == "T1"
    assert sent["kind"] == "blocked"
    assert sent["payload"] == "blocked on review"


async def test_request_board_sends_board_request() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.request_board()
    assert json.loads(ws.sent[-1])["type"] == "board_request"


async def test_advertise_sends_full_and_minimal_cards() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.advertise(
        description="quick", skills=["ollama"], task_classes=["chat"], model="m", meta={"k": 1}
    )
    full = json.loads(ws.sent[-1])
    assert full["type"] == "advertise"
    assert full["task_classes"] == ["chat"]
    assert full["model"] == "m"
    assert full["meta"] == {"k": 1}

    await agent.advertise()
    minimal = json.loads(ws.sent[-1])
    assert "skills" not in minimal
    assert "task_classes" not in minimal
    assert "model" not in minimal


async def test_request_manifest_sends_manifest_request() -> None:
    agent = SynapseAgent("A")
    ws = FakeWebSocket([])
    agent.connection = ws  # type: ignore[assignment]
    await agent.request_manifest()
    assert json.loads(ws.sent[-1])["type"] == "manifest_request"

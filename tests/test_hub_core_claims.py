# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - end-to-end tests for hub claims, releases, and task updates

from __future__ import annotations

from pathlib import Path

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub


async def test_claim_granted_is_broadcast_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        try:
            await alpha.agent.claim("T1", note="x")
            granted = await beta.recorder.wait_for(
                lambda m: m.get("type") == "claim_granted" and m.get("task_id") == "T1"
            )
            assert granted["owner"] == "ALPHA"
        finally:
            await close_agents(alpha, beta)


async def test_claim_denied_goes_to_second_agent_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        try:
            await alpha.agent.claim("T1")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            await beta.agent.claim("T1")
            denied = await beta.recorder.wait_for(lambda m: m.get("type") == "claim_denied")
            assert denied["task_id"] == "T1"
        finally:
            await close_agents(alpha, beta)


async def test_claim_with_invalid_ttl_falls_back_to_default_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            await alpha.agent.send_message("claim", task_id="T1", ttl_seconds="abc")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            assert hub.state.claims["T1"].owner == "ALPHA"
        finally:
            await close_agents(alpha)


async def test_claim_with_numeric_ttl_is_used_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            await alpha.agent.claim("T1", ttl_seconds=120)
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            assert "T1" in hub.state.claims
        finally:
            await close_agents(alpha)


async def test_release_granted_and_denied_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            await alpha.agent.claim("T1")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            await alpha.agent.release("T1")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "release_granted")
            await alpha.agent.release("GHOST")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "release_denied")
        finally:
            await close_agents(alpha)


async def test_release_granted_carries_receipt_and_records_progress_end_to_end() -> None:
    async with running_hub() as (hub, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        try:
            await alpha.agent.claim("T1")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            await alpha.agent.release(
                "T1",
                evidence=["pytest tests/test_hub_core_claims.py -q"],
                artifacts=["coverage.xml"],
                known_failures=["mkdocs pending on unrelated branch"],
                changed_files=["src/synapse_channel/core/handlers/leasing.py"],
                generated_artifacts=["docs/_generated/capability_manifest.json"],
                approvals=["reviewed-by=owner"],
                confidence="medium",
                freshness_seconds=30.0,
            )
            granted = await beta.recorder.wait_for(
                lambda m: m.get("type") == "release_granted" and m.get("task_id") == "T1"
            )
            progress = await beta.recorder.wait_for(
                lambda m: (
                    m.get("type") == "ledger_progress_posted"
                    and m.get("note", {}).get("task_id") == "T1"
                )
            )
        finally:
            await close_agents(alpha, beta)

    assert granted["receipt"] == {
        "artifacts": ["coverage.xml"],
        "approvals": ["reviewed-by=owner"],
        "changed_files": ["src/synapse_channel/core/handlers/leasing.py"],
        "confidence": "medium",
        "evidence": ["pytest tests/test_hub_core_claims.py -q"],
        "epistemic_reasons": ["known failures declared", "positive evidence present"],
        "epistemic_status": "degraded",
        "freshness_seconds": 30.0,
        "generated_artifacts": ["docs/_generated/capability_manifest.json"],
        "known_failures": ["mkdocs pending on unrelated branch"],
        "owner": "ALPHA",
        "released": True,
        "task_id": "T1",
    }
    assert progress["note"]["kind"] == "assessment"
    assert progress["note"]["text"] == (
        "release receipt: evidence=pytest tests/test_hub_core_claims.py -q; "
        "artifacts=coverage.xml; known_failures=mkdocs pending on unrelated branch; "
        "changed_files=src/synapse_channel/core/handlers/leasing.py; "
        "generated_artifacts=docs/_generated/capability_manifest.json; "
        "approvals=reviewed-by=owner; confidence=medium; freshness_seconds=30.0; "
        "epistemic_status=degraded; "
        "epistemic_reasons=known failures declared, positive evidence present"
    )
    assert hub.blackboard.progress[-1].text == progress["note"]["text"]


async def test_task_update_success_is_broadcast_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        beta = await connect_agent("BETA", uri)
        try:
            await alpha.agent.claim("T1")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
            await alpha.agent.update_task("T1", status="working", data_ref="r")
            updated = await beta.recorder.wait_for(lambda m: m.get("type") == "task_updated")
            assert updated["status"] == "working"
            assert updated["data_ref"] == "r"
            assert updated["version"] == 1
        finally:
            await close_agents(alpha, beta)


async def test_task_update_failure_errors_sender_end_to_end() -> None:
    async with running_hub() as (_, uri):
        alpha = await connect_agent("ALPHA", uri)
        try:
            await alpha.agent.update_task("MISSING")
            await alpha.recorder.wait_for(lambda m: m.get("type") == "error")
        finally:
            await close_agents(alpha)


async def test_release_receipt_progress_is_not_broadcast_when_the_board_refuses() -> None:
    """A refused progress append never turns into a broadcast note."""
    from synapse_channel.core.handlers.leasing import _record_release_receipt_progress
    from synapse_channel.core.receipts import build_release_receipt

    hub = SynapseHub(hub_id="syn-test")
    broadcasts: list[object] = []

    async def record_broadcast(data: object) -> None:
        broadcasts.append(data)

    hub._broadcast = record_broadcast  # type: ignore[method-assign]
    hub.blackboard.post_progress = (  # type: ignore[method-assign]
        lambda **_kwargs: (False, "quota exhausted")
    )
    receipt = build_release_receipt(task_id="T1", owner="alice")
    await _record_release_receipt_progress(hub, receipt)
    assert broadcasts == []


async def test_release_receipt_progress_broadcasts_without_a_journal() -> None:
    """An in-memory hub records the note as a broadcast, skipping the log."""
    from synapse_channel.core.handlers.leasing import _record_release_receipt_progress
    from synapse_channel.core.receipts import build_release_receipt

    hub = SynapseHub(hub_id="syn-test")  # journal is None by default
    broadcasts: list[dict[str, object]] = []

    async def record_broadcast(data: dict[str, object]) -> None:
        broadcasts.append(data)

    hub._broadcast = record_broadcast  # type: ignore[method-assign]
    receipt = build_release_receipt(task_id="T1", owner="alice")
    await _record_release_receipt_progress(hub, receipt)
    assert len(broadcasts) == 1
    assert broadcasts[0]["type"] == "ledger_progress_posted"


async def test_release_receipt_progress_records_into_a_journal(tmp_path: Path) -> None:
    """With a durable log the receipt note lands in the journal AND broadcasts."""
    from synapse_channel.core.handlers.leasing import _record_release_receipt_progress
    from synapse_channel.core.persistence import EventStore
    from synapse_channel.core.receipts import build_release_receipt

    journal = EventStore(tmp_path / "hub.db")
    try:
        hub = SynapseHub(hub_id="syn-test", journal=journal)
        broadcasts: list[dict[str, object]] = []

        async def record_broadcast(data: dict[str, object]) -> None:
            broadcasts.append(data)

        hub._broadcast = record_broadcast  # type: ignore[method-assign]
        receipt = build_release_receipt(task_id="T1", owner="alice")
        await _record_release_receipt_progress(hub, receipt)
        assert len(broadcasts) == 1
        kinds = [event.kind for event in journal.read_since(0)]
        assert "ledger_progress" in kinds
    finally:
        journal.close()
